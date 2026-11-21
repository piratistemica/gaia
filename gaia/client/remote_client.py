"""Cliente de `gaia serve`: consome SSE e desenha com o `TerminalUI`.

O cérebro fica no servidor. Este módulo é o espelho do `RemoteUI`: recebe
eventos e chama os mesmos métodos de interface que a TUI local chamaria. Assim
o desenho (Rich) não é reescrito — só a origem dos dados muda.

Uso:
    gaia --remote http://gaia:8787        # interativo
    gaia --remote http://gaia:8787 ask oi # um turno e sai
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

import httpx

from gaia.interface.terminal import TerminalUI

logger = logging.getLogger(__name__)

RECONECTAR_ESPERA = (1.0, 2.0, 5.0, 10.0)


class RemoteError(RuntimeError):
    """Falha de transporte com o `gaia serve`."""


class RemoteClient:
    """Fala HTTP com o serve e traduz eventos em chamadas de UI."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        ui: TerminalUI | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.token = token.strip()
        self.ui = ui or TerminalUI()
        self.timeout = timeout
        self._parar = threading.Event()
        self._alvo_run: str | None = None
        self._resposta = ""
        self.ultimo_seq = 0

    # ------------------------------------------------------------------ HTTP
    @property
    def cabecalhos(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _url(self, caminho: str) -> str:
        return f"{self.base}{caminho}"

    def _pedido(
        self, metodo: str, caminho: str, corpo: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as http:
                resposta = http.request(
                    metodo, self._url(caminho), json=corpo, headers=self.cabecalhos
                )
        except httpx.HTTPError as exc:
            raise RemoteError(f"nao consegui falar com {self.base}: {exc}") from exc
        if resposta.status_code == 401:
            raise RemoteError("token recusado pelo servidor (GAIA_SERVE_TOKEN)")
        if resposta.status_code >= 400:
            detalhe = ""
            try:
                detalhe = str(resposta.json().get("message") or "")
            except ValueError:
                detalhe = resposta.text[:200]
            raise RemoteError(f"{resposta.status_code}: {detalhe or 'erro'}")
        try:
            return dict(resposta.json())
        except ValueError:
            return {}

    def health(self) -> dict[str, Any]:
        return self._pedido("GET", "/health")

    def sessions(self) -> list[dict[str, Any]]:
        return list(self._pedido("GET", "/sessions").get("sessions") or [])

    def turn(self, texto: str) -> str:
        return str(self._pedido("POST", "/turn", {"text": texto}).get("run") or "")

    def decidir(self, request_id: str, decisao: str) -> None:
        self._pedido("POST", f"/permission/{request_id}", {"decision": decisao})

    def abortar(self) -> None:
        self._pedido("POST", "/abort")

    # ---------------------------------------------------------------- desenho
    def render(self, evento: dict[str, Any]) -> bool:
        """Desenha um evento. False = o servidor encerrou."""
        self.ultimo_seq = max(self.ultimo_seq, int(evento.get("seq") or 0))
        tipo = evento.get("type")
        ui = self.ui

        if tipo == "notice":
            nivel = evento.get("level")
            texto = str(evento.get("text") or "")
            if nivel == "success":
                ui.success(texto)
            elif nivel == "warning":
                ui.warning(texto)
            elif nivel == "error":
                ui.error(texto)
            elif nivel == "hint":
                ui.hint(texto)
            else:
                ui.info(texto)
        elif tipo == "say":
            ui.print_markdown(str(evento.get("text") or ""))
        elif tipo == "user":
            ui.print_user(str(evento.get("text") or ""))
        elif tipo == "proactive":
            ui.proactive(str(evento.get("text") or ""))
        elif tipo == "tool":
            ui.tool(str(evento.get("name") or ""), str(evento.get("detail") or ""))
        elif tipo == "reasoning":
            ui.reasoning(str(evento.get("text") or ""))
        elif tipo == "stream_begin":
            ui.begin_stream()
        elif tipo == "stream_delta":
            ui.token(str(evento.get("text") or ""))
        elif tipo == "stream_end":
            ui.end_stream()
        elif tipo == "status":
            rotulo = evento.get("text")
            if rotulo:
                ui.set_status(str(rotulo), str(evento.get("detail") or ""))
            else:
                ui.clear_status()
        elif tipo == "check":
            ui.check(
                bool(evento.get("ok")),
                str(evento.get("label") or ""),
                str(evento.get("detail") or ""),
            )
        elif tipo == "session":
            ui.info(
                f"sessao {evento.get('session')} · modelo {evento.get('model')} "
                f"· {evento.get('notes')} notas"
            )
        elif tipo == "tokens":
            ui.info(str(evento.get("text") or ""))
        elif tipo == "permission":
            self._responder_permissao(evento)
        elif tipo == "permission_timeout":
            ui.warning("a permissao expirou no servidor; a acao foi negada.")
        elif tipo == "run_finished":
            if not evento.get("ok"):
                ui.error(f"o turno falhou: {evento.get('error')}")
            if self._alvo_run and evento.get("run") == self._alvo_run:
                self._resposta = str(evento.get("text") or "")
                self._parar.set()
        elif tipo == "closed":
            return False
        return True

    def _responder_permissao(self, evento: dict[str, Any]) -> None:
        ferramenta = str(evento.get("tool") or "?")
        recursos = [str(item) for item in (evento.get("resources") or [])]
        decisao = self.ui.permission(ferramenta, recursos)
        request_id = str(evento.get("request_id") or "")
        if not request_id:
            return
        try:
            self.decidir(request_id, decisao)
        except RemoteError as exc:
            self.ui.warning(f"nao consegui responder a permissao: {exc}")

    # ------------------------------------------------------------------ SSE
    def stream(self) -> None:
        """Segue o SSE até o servidor encerrar; reconecta com `since`."""
        tentativa = 0
        while not self._parar.is_set():
            try:
                self._uma_conexao()
                return
            except (RemoteError, httpx.HTTPError, OSError) as exc:
                espera = RECONECTAR_ESPERA[min(tentativa, len(RECONECTAR_ESPERA) - 1)]
                tentativa += 1
                if self._parar.is_set():
                    return
                logger.debug("sse caiu (%s); reconectando em %.0fs", exc, espera)
                self.ui.warning(f"conexao caiu; reconectando em {espera:.0f}s")
                time.sleep(espera)

    def _uma_conexao(self) -> None:
        cabecalhos = dict(self.cabecalhos)
        cabecalhos["Accept"] = "text/event-stream"
        if self.ultimo_seq:
            cabecalhos["Last-Event-ID"] = str(self.ultimo_seq)
        with httpx.Client(timeout=None) as http:
            with http.stream(
                "GET", self._url("/events"), headers=cabecalhos
            ) as resposta:
                if resposta.status_code == 401:
                    raise RemoteError("token recusado (GAIA_SERVE_TOKEN)")
                if resposta.status_code >= 400:
                    raise RemoteError(f"events: HTTP {resposta.status_code}")
                for linha in resposta.iter_lines():
                    if self._parar.is_set():
                        return
                    if not linha or linha.startswith(":"):
                        continue
                    if not linha.startswith("data: "):
                        continue
                    try:
                        evento = json.loads(linha[len("data: ") :])
                    except ValueError:
                        continue
                    if not isinstance(evento, dict):
                        continue
                    if not self.render(evento):
                        return

    # -------------------------------------------------------------- comandos
    def pedir_um_turno(self, texto: str) -> str:
        """`gaia --remote URL ask ...`: um turno, desenha e devolve a resposta."""
        self._resposta = ""
        self._alvo_run = self.turn(texto)
        try:
            self.stream()
        finally:
            self._alvo_run = None
        return self._resposta

    def run(self) -> None:
        """Modo interativo: SSE numa thread, teclado na principal."""
        entradas: queue.Queue[str] = queue.Queue()

        def ler() -> None:
            while not self._parar.is_set():
                try:
                    texto = input()
                except EOFError:
                    self._parar.set()
                    return
                except KeyboardInterrupt:
                    self._parar.set()
                    return
                entradas.put(texto)

        leitor = threading.Thread(target=ler, daemon=True)
        leitor.start()
        servidor = threading.Thread(target=self._loop_servidor, daemon=True)
        servidor.start()
        try:
            while not self._parar.is_set():
                try:
                    texto = entradas.get(timeout=0.3)
                except queue.Empty:
                    continue
                texto = texto.strip()
                if not texto:
                    continue
                if texto in {"/sair", "/exit"}:
                    self._parar.set()
                    break
                try:
                    self.turn(texto)
                except RemoteError as exc:
                    self.ui.error(str(exc))
        finally:
            self._parar.set()
            self.ui.close()

    def _loop_servidor(self) -> None:
        try:
            self.stream()
        except Exception as exc:  # pragma: no cover - rede hostil
            logger.debug("stream encerrou: %s", exc)
            self.ui.error(f"conexao encerrada: {exc}")
            self._parar.set()
