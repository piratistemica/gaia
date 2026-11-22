"""Testes do modulo de provedores do painel."""

from __future__ import annotations

import pytest

from gaia.agent.providers.base import ProviderError
from gaia.agent.providers.openai_compat import OpenAICompatibleProvider
from gaia.config import Config
from gaia.control.providers import (
    agent_settings,
    credentials_summary,
    describe_providers,
)
from gaia.control.providers import (
    test_provider as check_provider,
)


@pytest.fixture
def cfg(tmp_path, isolated_env, monkeypatch):
    for nome in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "GAIA_AGENT_API_KEY",
    ):
        monkeypatch.delenv(nome, raising=False)
    cfg = Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.agent.provider = "openai"
    cfg.agent.model = "gpt-4o-mini"
    cfg.agent.api_key = ""
    return cfg


def test_lista_todos_os_presets(cfg):
    nomes = {item["name"] for item in describe_providers(cfg)}
    assert {"openai", "deepseek", "ollama", "commandcode"} <= nomes


def test_estado_da_chave(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste-abcdef1234")
    itens = {item["name"]: item for item in describe_providers(cfg)}
    assert itens["openai"]["has_key"] is True
    assert itens["openai"]["masked"] == "sk-…1234"
    assert itens["openai"]["current"] is True
    assert itens["deepseek"]["has_key"] is False


def test_nunca_devolve_a_chave(cfg, monkeypatch, recwarn):
    segredo = "sk-segredo-que-nao-pode-vazar-9999"
    monkeypatch.setenv("OPENAI_API_KEY", segredo)
    import json

    corpo = json.dumps(describe_providers(cfg))
    assert segredo not in corpo


def test_ollama_e_local_sem_chave(cfg):
    itens = {item["name"]: item for item in describe_providers(cfg)}
    assert itens["ollama"]["local"] is True
    assert itens["ollama"]["has_key"] is True


def test_agent_settings_traz_os_modelos(cfg):
    cfg.agent.fast_model = "gpt-4o-mini"
    cfg.agent.fallback_models = ["deepseek/deepseek-flash"]
    dados = agent_settings(cfg)
    assert dados["provider"] == "openai"
    assert dados["model"] == "gpt-4o-mini"
    assert dados["slug"] == "openai/gpt-4o-mini"
    assert dados["fallback_models"] == ["deepseek/deepseek-flash"]
    assert "api_key" not in dados


def test_credentials_summary(cfg, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-12345678")
    resumo = credentials_summary(cfg)
    assert resumo["current"]["name"] == "openai"
    assert "deepseek" in resumo["configured"]
    assert "openai" in resumo["missing_key"]


def test_fallback_models_default_vazio(cfg):
    assert cfg.agent.fallback_models == []


async def test_test_provider_sucesso(cfg, monkeypatch):
    async def fake_list_models(self):
        return ["gpt-4o-mini", "gpt-4o"]

    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", fake_list_models)
    resultado = await check_provider(cfg, "openai")
    assert resultado["ok"] is True
    assert resultado["status_code"] == 200
    assert resultado["models"] == ["gpt-4o-mini", "gpt-4o"]
    assert resultado["elapsed_ms"] >= 0


async def test_test_provider_401(cfg, monkeypatch):
    async def fake_list_models(self):
        raise ProviderError("Unauthorized", status_code=401)

    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", fake_list_models)
    resultado = await check_provider(cfg, "openai")
    assert resultado["ok"] is False
    assert resultado["status_code"] == 401
    assert resultado["error_kind"] == "auth"
    assert "Unauthorized" in resultado["message"]


async def test_test_provider_402(cfg, monkeypatch):
    async def fake_list_models(self):
        raise ProviderError("402 Insufficient Balance", status_code=402)

    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", fake_list_models)
    resultado = await check_provider(cfg, "openai")
    assert resultado["ok"] is False
    assert resultado["status_code"] == 402
    assert resultado["error_kind"] == "auth"


async def test_test_provider_timeout(cfg, monkeypatch):
    import asyncio

    async def fake_list_models(self):
        await asyncio.sleep(5)

    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", fake_list_models)
    resultado = await check_provider(cfg, "openai", timeout=0.05)
    assert resultado["ok"] is False
    assert resultado["error_kind"] == "network"
    assert "sem resposta" in resultado["message"]


async def test_test_provider_desconhecido(cfg):
    resultado = await check_provider(cfg, "nao-existe")
    assert resultado["ok"] is False
    assert resultado["error_kind"] == "config"
