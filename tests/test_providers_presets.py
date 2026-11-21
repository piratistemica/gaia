from __future__ import annotations

import json

import pytest

from gaia.agent import catalog
from gaia.agent.providers import (
    PRESETS,
    describe_provider,
    normalize_provider,
    preset_api_key,
    resolve_api_key,
)


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch):
    for preset in PRESETS.values():
        for key in preset.env_keys:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("GAIA_AGENT_API_KEY", raising=False)


def test_aliases_apontam_para_presets():
    assert normalize_provider("cc") == "commandcode"
    assert normalize_provider("goat") == "commandcode"
    assert normalize_provider("cc-claude") == "commandcode-anthropic"
    assert normalize_provider("zen") == "opencode"
    assert normalize_provider("Claude") == "anthropic"


def test_base_urls_da_command_code():
    assert describe_provider("commandcode")["base_url"] == (
        "https://api.commandcode.ai/provider/v1"
    )
    assert describe_provider("commandcode-anthropic")["api"] == "anthropic"
    assert describe_provider("commandcode-anthropic")["base_url"] == (
        "https://api.commandcode.ai/provider"
    )


def test_chave_lida_do_auth_file(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"apiKey": "user_abc"}), encoding="utf-8")
    monkeypatch.setattr(
        "gaia.agent.providers.factory.PRESETS",
        {**PRESETS, "commandcode": _with_auth(PRESETS["commandcode"], auth)},
    )

    assert preset_api_key("commandcode") == "user_abc"
    assert resolve_api_key("commandcode") == "user_abc"


def test_env_vence_o_auth_file(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"apiKey": "user_arquivo"}), encoding="utf-8")
    monkeypatch.setattr(
        "gaia.agent.providers.factory.PRESETS",
        {**PRESETS, "commandcode": _with_auth(PRESETS["commandcode"], auth)},
    )
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "user_env")

    assert preset_api_key("commandcode") == "user_env"


def test_auth_file_ausente_nao_quebra(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gaia.agent.providers.factory.PRESETS",
        {
            **PRESETS,
            "commandcode": _with_auth(PRESETS["commandcode"], tmp_path / "sumiu.json"),
        },
    )
    assert preset_api_key("commandcode") == ""


def test_commandcode_entra_no_catalogo_mesmo_sem_env(monkeypatch):
    monkeypatch.setattr(
        "gaia.agent.catalog._own_key", lambda name: ""
    )
    assert catalog._has_own_key("commandcode") is True
    assert catalog._has_own_key("openrouter") is False


def _with_auth(preset, path):
    from dataclasses import replace

    return replace(preset, auth_file=(str(path),))
