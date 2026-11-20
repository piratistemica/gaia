"""HTTP + SSE do `zaya serve`.

Este modulo nao conhece o agente: fala com um `Target` (ver `protocolo`).
O transporte e proposital e minimalista — stdlib, sem dependencia nova:

* ``GET /events``  SSE com ``id:`` (seq) e ``event:`` (tipo); ``?since=`` ou
  ``Last-Event-ID`` retomam de onde o cliente parou.
* ``POST /turn``            ``{"text": "..."}`` -> ``202 {"run": "..."}``
* ``POST /permission/{id}`` ``{"decision": "once|always|reject"}``
* ``POST /abort``
* ``GET  /health``, ``GET /sessions``

O `RemoteUI` e a fonte dos eventos: cada assinante tem sua fila, e o handler
apenas serializa. Sem estado compartilhado alem do `RemoteUI`.
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from zaya.interface.remote import RemoteUI

logger = logging.getLogger(__name__)

DECISIONS = {"once", "always", "reject"}
HEARTBEAT_SECONDS = 15.0


class Target(Protocol):
    """O que o transporte precisa saber do motor. `zaya/serve/app.py` liga nisso."""

    def health(self) -> dict[str, Any]: ...

    def sessions(self) -> list[dict[str, Any]]: ...

    def turn(self, text: str) -> str:
        """Agenda um turno e devolve o id do run (nao espera terminar)."""

    def decide(self, request_id: str, decision: str) -> None: ...

    def abort(self) -> None: ...


class ServeConfigError(ValueError):
    """Configuracao que exporia o servidor (bind na rede sem token)."""


def _eh_loopback(host: str) -> bool:
    return host in {"", "127.0.0.1", "localhost", "::1"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "zaya-serve"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- utilidades
    @property
    def app(self) -> ServeServer:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # silencio no teste
        logger.debug("serve: " + fmt, *args)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _erro(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"error": code, "message": message})

    def _autorizado(self) -> bool:
        token = self.app.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        prefixo = "Bearer "
        if not header.startswith(prefixo):
            return False
        return header[len(prefixo) :].strip() == token

    def _corpo(self) -> dict[str, Any] | None:
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho <= 0:
            return {}
        if tamanho > 1_000_000:
            return None
        try:
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
        except ValueError:
            return None
        return dados if isinstance(dados, dict) else None

    # ------------------------------------------------------------------- rotas
    def do_GET(self) -> None:
        url = urlparse(self.path)
        rota = url.path.rstrip("/") or "/"
        if rota == "/health":
            # Uma falha aqui não pode virar "empty reply": o cliente precisa
            # de JSON para saber que o motor está quebrado (e o comando
            # `zmin serve status` depende disso).
            try:
                saude = self.app.target.health()
            except Exception as exc:
                logger.warning("health falhou: %s", exc, exc_info=True)
                self._erro(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "health_failed", str(exc)
                )
                return
            self._json(HTTPStatus.OK, {"ok": True, **saude})
            return
        if rota == "/events":
            if not self._autorizado():
                self._erro(HTTPStatus.UNAUTHORIZED, "unauthorized", "token invalido")
                return
            self._eventos(parse_qs(url.query))
            return
        if rota == "/sessions":
            if not self._autorizado():
                self._erro(HTTPStatus.UNAUTHORIZED, "unauthorized", "token invalido")
                return
            self._json(HTTPStatus.OK, {"sessions": self.app.target.sessions()})
            return
        self._erro(HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        rota = url.path.rstrip("/") or "/"
        if not self._autorizado():
            self._erro(HTTPStatus.UNAUTHORIZED, "unauthorized", "token invalido")
            return
        corpo = self._corpo()
        if corpo is None:
            self._erro(HTTPStatus.BAD_REQUEST, "bad_json", "corpo JSON invalido")
            return
        if rota == "/turn":
            texto = str(corpo.get("text") or "").strip()
            if not texto:
                self._erro(HTTPStatus.BAD_REQUEST, "empty_text", "texto vazio")
                return
            try:
                run = self.app.target.turn(texto)
            except RuntimeError as exc:  # turno ja em andamento
                self._erro(HTTPStatus.CONFLICT, "busy", str(exc))
                return
            self._json(HTTPStatus.ACCEPTED, {"run": run})
            return
        if rota == "/abort":
            self.app.target.abort()
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if rota.startswith("/permission/"):
            request_id = rota[len("/permission/") :]
            decisao = str(corpo.get("decision") or "")
            if not request_id:
                self._erro(HTTPStatus.BAD_REQUEST, "missing_id", "request_id vazio")
                return
            if decisao not in DECISIONS:
                self._erro(
                    HTTPStatus.BAD_REQUEST,
                    "bad_decision",
                    "decision deve ser once, always ou reject",
                )
                return
            # Quem espera é o `RemoteUI` (a thread do turno está bloqueada nele),
            # então o transporte destrava primeiro e avisa o alvo depois, para
            # ele registrar/encaminhar se quiser.
            resolvido = self.app.ui.resolve_permission(request_id, decisao)
            self.app.target.decide(request_id, decisao)
            self._json(HTTPStatus.OK, {"ok": True, "resolved": resolvido})
            return
        self._erro(HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}")

    # --------------------------------------------------------------------- SSE
    def _eventos(self, query: dict[str, list[str]]) -> None:
        since: int | None = None
        brutos = query.get("since") or []
        if brutos:
            try:
                since = int(brutos[0])
            except ValueError:
                since = None
        if since is None:
            cabecalho = self.headers.get("Last-Event-ID")
            if cabecalho:
                try:
                    since = int(cabecalho)
                except ValueError:
                    since = None
        fila = self.app.ui.subscribe(since=since)
        # Assinar ANTES dos cabecalhos: o cliente desbloqueia no header e pode
        # emitir imediatamente; assinar depois perderia esse evento.
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while not self.app.parar.is_set():
                try:
                    evento = fila.get(timeout=HEARTBEAT_SECONDS)
                except Exception:  # Empty: mantem a conexao viva
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._escrever(evento)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.app.ui.unsubscribe(fila)

    def _escrever(self, evento: dict[str, Any]) -> None:
        dados = json.dumps(evento, ensure_ascii=False)
        bloco = (
            f"id: {evento.get('seq', 0)}\n"
            f"event: {evento.get('type', 'message')}\n"
            f"data: {dados}\n\n"
        )
        self.wfile.write(bloco.encode("utf-8"))
        self.wfile.flush()


class ServeServer:
    """Dono do socket. `start()` sobe em thread e devolve a URL base."""

    def __init__(
        self,
        target: Target,
        ui: RemoteUI,
        *,
        host: str = "127.0.0.1",
        port: int = 8787,
        token: str = "",
    ) -> None:
        if not _eh_loopback(host) and not token.strip():
            raise ServeConfigError(
                f"recusando escutar em {host} sem token: defina ZAYA_SERVE_TOKEN"
            )
        self.target = target
        self.ui = ui
        self.token = token.strip()
        self.parar = threading.Event()
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.app = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ ciclo
    def serve_forever(self) -> None:
        self._httpd.serve_forever(poll_interval=0.2)

    def start(self) -> str:
        self._thread = threading.Thread(
            target=self.serve_forever, name="zaya-serve", daemon=True
        )
        self._thread.start()
        return self.base_url

    def shutdown(self) -> None:
        self.parar.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ dados
    @property
    def address(self) -> tuple[str, int]:
        host, porta = self._httpd.server_address[:2]
        return str(host), int(porta)

    @property
    def base_url(self) -> str:
        host, porta = self.address
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        return f"http://{host}:{porta}"

