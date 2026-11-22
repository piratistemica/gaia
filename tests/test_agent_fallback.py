"""Testes do fallback minimo de modelo (401/402/403)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gaia.agent import AgentClient
from gaia.agent.errors import AgentError
from gaia.agent.providers.base import (
    ChatRequest,
    Finish,
    ProviderError,
    TextDelta,
)
from gaia.agent.providers.openai_compat import OpenAICompatibleProvider
from gaia.config import Config


class FakeProvider:
    """Provedor que falha com o status pedido e depois responde texto."""

    def __init__(self, status: int | None = None, texto: str = "ok") -> None:
        self.status = status
        self.texto = texto
        self.chamadas: list[str] = []
        self.fechado = False

    async def stream(self, request: ChatRequest):
        self.chamadas.append(request.model)
        if self.status is not None:
            raise ProviderError(
                f"{self.status} erro", status_code=self.status
            )
        yield TextDelta(text=self.texto)
        yield Finish(reason="stop")

    async def list_models(self) -> list[str]:
        return ["modelo-1"]

    async def ping(self) -> dict[str, Any]:
        return {"models": ["modelo-1"]}

    async def aclose(self) -> None:
        self.fechado = True


@pytest.fixture
def cfg(tmp_path, isolated_env, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_API_KEY", "sk-principal-1234")
    cfg = Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.agent.provider = "openai"
    cfg.agent.model = "gpt-4o-mini"
    cfg.agent.api_key = ""
    cfg.agent.max_steps = 2
    return cfg


def _instalar(agent: AgentClient, **provedores: FakeProvider) -> None:
    """Troca ``_ensure_provider`` e ``_fallback_provider`` por fakes."""
    principal = provedores["principal"]

    def _ensure() -> Any:
        return principal

    agent._provider = principal
    agent._ensure_provider = _ensure  # type: ignore[method-assign]

    def _fallback(slug: str) -> Any:
        alvo = slug.split("/", 1)[1] if "/" in slug else slug
        fake = provedores.get("alternativo")
        if fake is None:
            return None
        return fake, type("Ref", (), {"provider_id": "deepseek", "model_id": alvo})()

    agent._fallback_provider = _fallback  # type: ignore[method-assign]


async def test_402_troca_para_fallback_e_emite_evento(cfg, monkeypatch):
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    principal = FakeProvider(status=402)
    alternativo = FakeProvider(texto="resposta do alternativo")

    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(agent, principal=principal, alternativo=alternativo)
    monkeypatch.setattr(
        OpenAICompatibleProvider, "stream", lambda self, req: None
    )  # nao usado

    request = ChatRequest(model="gpt-4o-mini", messages=[])
    eventos: list[Any] = []
    textos = [
        delta.text
        async for delta in agent._stream_with_fallback(request, eventos.append)
        if isinstance(delta, TextDelta)
    ]

    assert textos == ["resposta do alternativo"]
    assert principal.chamadas == ["gpt-4o-mini"]
    assert alternativo.chamadas == ["deepseek-flash"]
    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.from_slug == "openai/gpt-4o-mini"
    assert evento.to_slug == "deepseek/deepseek-flash"
    assert evento.status_code == 402
    assert "402" in evento.reason


async def test_401_tambem_faz_fallback(cfg):
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(
        agent,
        principal=FakeProvider(status=401),
        alternativo=FakeProvider(texto="ok"),
    )
    request = ChatRequest(model="gpt-4o-mini", messages=[])
    textos = [
        delta.text
        async for delta in agent._stream_with_fallback(request, lambda _e: None)
        if isinstance(delta, TextDelta)
    ]
    assert textos == ["ok"]


async def test_500_nao_faz_fallback(cfg):
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    principal = FakeProvider(status=500)
    alternativo = FakeProvider(texto="nao deveria")
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(agent, principal=principal, alternativo=alternativo)

    request = ChatRequest(model="gpt-4o-mini", messages=[])
    with pytest.raises(ProviderError):
        async for _ in agent._stream_with_fallback(request, lambda _e: None):
            pass
    assert alternativo.chamadas == []


async def test_429_nao_faz_fallback(cfg):
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(
        agent,
        principal=FakeProvider(status=429),
        alternativo=FakeProvider(),
    )
    request = ChatRequest(model="gpt-4o-mini", messages=[])
    with pytest.raises(ProviderError):
        async for _ in agent._stream_with_fallback(request, lambda _e: None):
            pass


async def test_sem_fallback_configurado_propaga(cfg):
    cfg.agent.fallback_models = []
    principal = FakeProvider(status=402)
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(agent, principal=principal)
    request = ChatRequest(model="gpt-4o-mini", messages=[])
    with pytest.raises(ProviderError):
        async for _ in agent._stream_with_fallback(request, lambda _e: None):
            pass


async def test_alternativo_que_tambem_falha_propaga(cfg):
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    alternativo = FakeProvider(status=402)
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(agent, principal=FakeProvider(status=402), alternativo=alternativo)
    request = ChatRequest(model="gpt-4o-mini", messages=[])
    with pytest.raises(ProviderError):
        async for _ in agent._stream_with_fallback(request, lambda _e: None):
            pass
    assert alternativo.chamadas == ["deepseek-flash"]


async def test_fallback_fecha_o_provedor_alternativo(cfg):
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    alternativo = FakeProvider(texto="ok")
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(agent, principal=FakeProvider(status=402), alternativo=alternativo)
    request = ChatRequest(model="gpt-4o-mini", messages=[])
    async for _ in agent._stream_with_fallback(request, lambda _e: None):
        pass
    assert alternativo.fechado is True


async def test_config_nao_muda_apos_fallback(cfg):
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    _instalar(
        agent, principal=FakeProvider(status=402), alternativo=FakeProvider()
    )
    request = ChatRequest(model="gpt-4o-mini", messages=[])
    async for _ in agent._stream_with_fallback(request, lambda _e: None):
        pass
    assert cfg.agent.provider == "openai"
    assert cfg.agent.model == "gpt-4o-mini"


async def test_sem_chave_nao_e_provider_error(cfg, monkeypatch):
    """Sem chave o erro e AgentError na construcao: nao tenta fallback."""
    monkeypatch.delenv("GAIA_AGENT_API_KEY", raising=False)
    cfg.agent.api_key = ""
    cfg.agent.provider = "groq"
    agent = AgentClient(cfg, Path(cfg.resolved_data_dir()))
    with pytest.raises(AgentError):
        agent._ensure_provider()
