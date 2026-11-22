"""Provedores para o painel: presets, estado da credencial e teste real.

Nenhuma funcao devolve chave: so ``has_key``, origem e a mascara do painel de
secrets. O teste de conexao bate na API de verdade (``GET /models``) — um
"testar" que sempre responde ok nao serve para diagnosticar 401/402.

Nada aqui muda o config: a troca de modelo continua passando pelo caminho
existente do ``/modelo`` (``Orchestrator.model_messages`` +
``save_agent_model``), que preserva o TOML.
"""

from __future__ import annotations

import time
from typing import Any

from gaia.agent.errors import AgentError
from gaia.agent.providers import (
    PRESETS,
    PUBLIC_PROVIDERS,
    ProviderError,
    build_provider,
    normalize_provider,
    resolve_api_key,
)
from gaia.config import Config
from gaia.control.secrets import mask


# Aliases historicos nao contam como provedor do catalogo (sao o mesmo preset).
def _catalogo() -> list[str]:
    nomes: list[str] = []
    for nome in PUBLIC_PROVIDERS:
        if nome not in nomes:
            nomes.append(nome)
    return nomes


def _auth_source(provider: str, cfg: Config) -> str:
    """De onde a chave veio, sem revelar o valor."""
    preset = PRESETS.get(normalize_provider(provider))
    if getattr(cfg.agent, "api_key", "").strip() and (
        normalize_provider(provider) == normalize_provider(cfg.agent.provider)
    ):
        return "config"
    if preset is not None:
        import os

        for nome in preset.env_keys:
            if os.environ.get(nome, "").strip():
                return nome
        for caminho in preset.auth_file:
            from pathlib import Path

            if Path(caminho).expanduser().is_file():
                return f"arquivo {caminho}"
    return ""


def describe_providers(cfg: Config) -> list[dict[str, Any]]:
    """Todos os presets com estado da credencial e o atual marcado."""
    atual = normalize_provider(cfg.agent.provider)
    itens: list[dict[str, Any]] = []
    for nome in _catalogo():
        preset = PRESETS.get(nome)
        if preset is None:  # pragma: no cover - PUBLIC_PROVIDERS vem de PRESETS
            continue
        chave = resolve_api_key(nome, cfg.agent.api_key if nome == atual else "")
        sem_chave_necessaria = not preset.env_keys and not preset.auth_file
        origem = _auth_source(nome, cfg)
        itens.append(
            {
                "name": nome,
                "api": preset.api,
                "base_url": preset.base_url,
                "current": nome == atual,
                "model": cfg.agent.model if nome == atual else "",
                "fast_model": cfg.agent.fast_model if nome == atual else "",
                "has_key": bool(chave) or sem_chave_necessaria,
                "masked": mask(chave) if chave else "",
                "env_keys": list(preset.env_keys),
                "auth_file": list(preset.auth_file),
                "auth_source": origem,
                "default_model": preset.default_model,
                "local": not preset.env_keys and not preset.auth_file,
            }
        )
    return itens


def agent_settings(cfg: Config) -> dict[str, Any]:
    """Bloco de modelos do agente (principal, rapido, resumo, fallback)."""
    return {
        "provider": normalize_provider(cfg.agent.provider),
        "model": cfg.agent.model,
        "slug": cfg.agent.model_slug(),
        "fast_model": cfg.agent.fast_model,
        "history_summary_model": cfg.agent.history_summary_model,
        "fallback_models": list(cfg.agent.fallback_models),
        "temperature": cfg.agent.temperature,
        "max_tokens": cfg.agent.max_tokens,
        "max_steps": cfg.agent.max_steps,
        "request_timeout": cfg.agent.request_timeout,
        "stall_timeout": cfg.agent.stall_timeout,
        "base_url": cfg.agent.base_url,
        "auto_approve": cfg.agent.auto_approve,
        "tools": cfg.agent.tools,
    }


async def test_provider(
    cfg: Config, provider: str, *, timeout: float = 15.0
) -> dict[str, Any]:
    """Testa a conexao de verdade e devolve o erro real, classificado.

    Nunca levanta: ``{ok: False, status_code, message, error_kind}`` e resposta
    (a tela precisa mostrar 401, 402, timeout).
    """
    import asyncio

    nome = normalize_provider(provider)
    if nome not in PRESETS:
        return {
            "ok": False,
            "provider": nome,
            "status_code": 0,
            "message": f"provider desconhecido: {nome}",
            "error_kind": "config",
            "models": [],
            "elapsed_ms": 0,
        }
    parcial = cfg.agent.model_copy(
        update={
            "provider": nome,
            "api_key": cfg.agent.api_key if nome == normalize_provider(cfg.agent.provider) else "",
        }
    )
    inicio = time.monotonic()
    try:
        adaptador = build_provider(parcial)
    except AgentError as exc:
        return {
            "ok": False,
            "provider": nome,
            "status_code": 0,
            "message": str(exc),
            "error_kind": "config",
            "models": [],
            "elapsed_ms": _ms(inicio),
        }
    except Exception as exc:  # pragma: no cover - defensivo
        return {
            "ok": False,
            "provider": nome,
            "status_code": 0,
            "message": str(exc),
            "error_kind": "config",
            "models": [],
            "elapsed_ms": _ms(inicio),
        }

    try:
        modelos = await asyncio.wait_for(adaptador.list_models(), timeout=timeout)
    except TimeoutError:
        return {
            "ok": False,
            "provider": nome,
            "status_code": 0,
            "message": f"sem resposta em {timeout:.0f}s",
            "error_kind": "network",
            "models": [],
            "elapsed_ms": _ms(inicio),
        }
    except ProviderError as exc:
        from gaia.control.runs import classify_error

        return {
            "ok": False,
            "provider": nome,
            "status_code": int(exc.status_code or 0),
            "message": str(exc),
            "error_kind": classify_error(str(exc)),
            "models": [],
            "elapsed_ms": _ms(inicio),
        }
    except Exception as exc:
        from gaia.control.runs import classify_error

        return {
            "ok": False,
            "provider": nome,
            "status_code": 0,
            "message": str(exc),
            "error_kind": classify_error(str(exc)),
            "models": [],
            "elapsed_ms": _ms(inicio),
        }
    finally:
        fechar = getattr(adaptador, "aclose", None)
        if callable(fechar):
            try:
                await fechar()
            except Exception:  # pragma: no cover - fechar conexao nao e erro
                pass

    lista = [str(item) for item in modelos][:200]
    return {
        "ok": True,
        "provider": nome,
        "status_code": 200,
        "message": f"{len(lista)} modelo(s) disponivel(is)",
        "error_kind": "",
        "models": lista,
        "elapsed_ms": _ms(inicio),
    }


def _ms(inicio: float) -> int:
    return max(0, int((time.monotonic() - inicio) * 1000))


def credentials_summary(cfg: Config) -> dict[str, Any]:
    """Resumo para o Overview: quem esta ativo e quem tem credencial."""
    itens = describe_providers(cfg)
    atuais = [item for item in itens if item["current"]]
    atual = atuais[0] if atuais else None
    return {
        "current": atual,
        "configured": [item["name"] for item in itens if item["has_key"]],
        "missing_key": [
            item["name"]
            for item in itens
            if not item["has_key"] and not item["local"]
        ],
    }


__all__ = [
    "agent_settings",
    "credentials_summary",
    "describe_providers",
    "test_provider",
]
