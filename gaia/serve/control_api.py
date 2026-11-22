"""Rotas ``/control/api/*`` do Control Center.

Separadas do ``server.py`` para o transporte continuar legivel: aqui so mora a
traducao rota -> metodo do ``ControlCenter`` -> JSON. Autorizacao, corpo e
resposta continuam sendo do handler (que ja sabe falar HTTP).
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

logger = logging.getLogger("gaia.control.api")


def _int(query: dict[str, list[str]], nome: str, padrao: int, teto: int = 500) -> int:
    try:
        valor = int((query.get(nome) or [str(padrao)])[0])
    except (TypeError, ValueError):
        return padrao
    return max(1, min(valor, teto))


def _texto(query: dict[str, list[str]], nome: str) -> str:
    return (query.get(nome) or [""])[0].strip()


def get(handler: Any, rota: str, query: dict[str, list[str]]) -> None:
    """GET do painel. ``handler`` e o ``_Handler`` do serve."""
    if not handler._autorizado_controle():
        handler._erro(
            HTTPStatus.UNAUTHORIZED, "unauthorized", "token do painel invalido"
        )
        return
    centro = handler.app.center
    if centro is None:  # pragma: no cover - defensivo
        handler._erro(
            HTTPStatus.SERVICE_UNAVAILABLE, "no_center", "control center indisponivel"
        )
        return
    try:
        if rota == "/control/api/overview":
            handler._json(HTTPStatus.OK, centro.overview())
            return
        if rota == "/control/api/health":
            handler._json(HTTPStatus.OK, centro.health())
            return
        if rota == "/control/api/skills":
            handler._json(HTTPStatus.OK, centro.skills())
            return
        if rota == "/control/api/automations":
            handler._json(HTTPStatus.OK, centro.automations())
            return
        if rota == "/control/api/runs":
            handler._json(
                HTTPStatus.OK,
                centro.activity(
                    limit=_int(query, "limit", 50),
                    kind=_texto(query, "kind"),
                    status=_texto(query, "status"),
                ),
            )
            return
        if rota == "/control/api/errors":
            handler._json(
                HTTPStatus.OK, centro.errors(limit=_int(query, "limit", 50))
            )
            return
        if rota == "/control/api/providers":
            handler._json(HTTPStatus.OK, centro.providers())
            return
        if rota == "/control/api/settings":
            handler._json(HTTPStatus.OK, centro.settings())
            return
        if rota == "/control/api/secrets":
            handler._json(HTTPStatus.OK, centro.secrets())
            return
        if rota == "/control/api/models":
            handler._json(HTTPStatus.OK, _aguardar(handler, centro.list_models()))
            return
    except Exception as exc:  # painel nao derruba o motor
        logger.warning("control: falha em %s: %s", rota, exc, exc_info=True)
        handler._erro(
            HTTPStatus.INTERNAL_SERVER_ERROR, "control_failed", str(exc)
        )
        return
    handler._erro(
        HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}"
    )


def post(handler: Any, rota: str, corpo: dict[str, Any]) -> None:
    """POST/PUT/PATCH/DELETE do painel."""
    if not handler._autorizado_controle():
        handler._erro(
            HTTPStatus.UNAUTHORIZED, "unauthorized", "token do painel invalido"
        )
        return
    centro = handler.app.center
    if centro is None:  # pragma: no cover - defensivo
        handler._erro(
            HTTPStatus.SERVICE_UNAVAILABLE, "no_center", "control center indisponivel"
        )
        return

    try:
        if rota == "/control/api/model":
            slug = str(corpo.get("model") or "").strip()
            if not slug:
                handler._erro(
                    HTTPStatus.BAD_REQUEST, "empty_model", "modelo vazio"
                )
                return
            handler._json(HTTPStatus.OK, centro.switch_model(slug))
            return
        if rota.startswith("/control/api/providers/") and rota.endswith("/test"):
            nome = rota[len("/control/api/providers/") : -len("/test")].strip("/")
            if not nome:
                handler._erro(
                    HTTPStatus.BAD_REQUEST, "missing_provider", "provider vazio"
                )
                return
            resultado = _aguardar(handler, centro.test_provider(nome))
            handler._json(HTTPStatus.OK, resultado)
            return
        if rota == "/control/api/settings":
            secao = str(corpo.get("section") or "").strip()
            chave = str(corpo.get("key") or "").strip()
            if not secao or not chave:
                handler._erro(
                    HTTPStatus.BAD_REQUEST,
                    "missing_target",
                    "informe section e key",
                )
                return
            if "value" not in corpo:
                handler._erro(
                    HTTPStatus.BAD_REQUEST, "missing_value", "informe value"
                )
                return
            handler._json(
                HTTPStatus.OK, centro.update_setting(secao, chave, corpo["value"])
            )
            return
        if rota == "/control/api/settings/override":
            secao = str(corpo.get("section") or "").strip()
            chave = str(corpo.get("key") or "").strip()
            if not secao or not chave:
                handler._erro(
                    HTTPStatus.BAD_REQUEST,
                    "missing_target",
                    "informe section e key",
                )
                return
            handler._json(
                HTTPStatus.OK, centro.remove_override(secao, chave)
            )
            return
        if rota.startswith("/control/api/secrets/"):
            chave = rota[len("/control/api/secrets/") :].strip("/")
            if not chave:
                handler._erro(
                    HTTPStatus.BAD_REQUEST, "missing_key", "chave vazia"
                )
                return
            handler._json(
                HTTPStatus.OK, centro.set_secret(chave, str(corpo.get("value") or ""))
            )
            return
    except ValueError as exc:
        # Valor recusado (sensivel, fora da faixa, override no env): 422 com
        # a mensagem exata, porque ela e a instrucao para a pessoa corrigir.
        handler._erro(
            HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_setting", str(exc)
        )
        return
    except FileNotFoundError as exc:
        handler._erro(HTTPStatus.NOT_FOUND, "missing_file", str(exc))
        return
    except RuntimeError as exc:
        handler._erro(HTTPStatus.CONFLICT, "busy", str(exc))
        return
    except Exception as exc:
        logger.warning("control: falha em %s: %s", rota, exc, exc_info=True)
        handler._erro(
            HTTPStatus.INTERNAL_SERVER_ERROR, "control_failed", str(exc)
        )
        return
    handler._erro(
        HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}"
    )


def delete(handler: Any, rota: str) -> None:
    """DELETE do painel (remover credencial = comentar no .env)."""
    if not handler._autorizado_controle():
        handler._erro(
            HTTPStatus.UNAUTHORIZED, "unauthorized", "token do painel invalido"
        )
        return
    centro = handler.app.center
    if centro is None:  # pragma: no cover - defensivo
        handler._erro(
            HTTPStatus.SERVICE_UNAVAILABLE, "no_center", "control center indisponivel"
        )
        return
    if not rota.startswith("/control/api/secrets/"):
        handler._erro(
            HTTPStatus.NOT_FOUND, "not_found", f"rota desconhecida: {rota}"
        )
        return
    chave = rota[len("/control/api/secrets/") :].strip("/")
    if not chave:
        handler._erro(
            HTTPStatus.BAD_REQUEST, "missing_key", "chave vazia"
        )
        return
    try:
        handler._json(HTTPStatus.OK, centro.remove_secret(chave))
    except ValueError as exc:
        handler._erro(
            HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_key", str(exc)
        )
    except FileNotFoundError as exc:
        handler._erro(HTTPStatus.NOT_FOUND, "missing_file", str(exc))


def _aguardar(handler: Any, resultado: Any) -> Any:
    """Espera um awaitable rodando no loop do motor, sem bloquear o HTTP."""
    if not hasattr(resultado, "__await__"):
        return resultado
    esperar = getattr(handler.app.target, "run_awaitable", None)
    if esperar is None:
        raise RuntimeError("motor sem runner para operacoes assincronas")
    return esperar(resultado)


__all__ = ["delete", "get", "post"]
