"""Rotas do Control Center: autorizacao separada e dados reais."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any

import pytest

from gaia.config import Config
from gaia.control.center import ControlCenter
from gaia.interface.remote import RemoteUI
from gaia.memory.store import MemoryStore
from gaia.serve.server import ServeServer


def pedir(
    base: str,
    metodo: str,
    caminho: str,
    *,
    token: str | None = None,
    corpo: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    host, porta = base.removeprefix("http://").split(":")
    conexao = http.client.HTTPConnection(host, int(porta), timeout=10)
    cabecalhos: dict[str, str] = {}
    if token is not None:
        cabecalhos["Authorization"] = f"Bearer {token}"
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        cabecalhos["Content-Type"] = "application/json"
    conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conexao.getresponse()
    bruto = resposta.read()
    conexao.close()
    try:
        return resposta.status, json.loads(bruto)
    except ValueError:
        return resposta.status, bruto


class FakeTarget:
    def __init__(self) -> None:
        self.health = lambda: {"model": "openai/teste"}  # type: ignore[assignment]

    def sessions(self) -> list[dict[str, Any]]:
        return []

    def models(self) -> list[dict[str, Any]]:
        return []

    def switch_model(self, slug: str) -> list[str]:
        return [f"modelo: {slug}"]

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    def turn(self, text: str) -> str:
        return "run_1"

    def decide(self, request_id: str, decision: str) -> None:
        return None

    def abort(self) -> None:
        return None

    def run_awaitable(self, aguardavel: Any, timeout: float = 60.0) -> Any:
        import asyncio

        return asyncio.run(aguardavel) if asyncio.iscoroutine(aguardavel) else aguardavel


@pytest.fixture
def setup(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    cfg.telegram.enabled = True
    cfg.telegram.bot_token = "1:abc"
    cfg.telegram.allowed_chat_id = "42"
    cfg.secondbrain.path = str(tmp_path / "brain")
    Path(cfg.secondbrain.path).mkdir(parents=True)
    store = MemoryStore(tmp_path / "gaia.db")
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-teste-12345678\n")
    centro = ControlCenter(
        cfg,
        store,
        env_path=env,
        config_path=tmp_path / "config.toml",
    )
    alvo = FakeTarget()
    servidor = ServeServer(
        alvo,  # type: ignore[arg-type]
        RemoteUI(),
        host="127.0.0.1",
        port=0,
        token="tok-chat",
        control_token="ctl-secreto",
        center=centro,
    )
    base = servidor.start()
    yield base, centro, store, env
    servidor.shutdown()
    store.close()


def test_pagina_control_e_publica(setup):
    base, *_ = setup
    status, corpo = pedir(base, "GET", "/control")
    assert status == 200
    assert b"Control Center" in corpo


def test_assets_do_painel(setup):
    base, *_ = setup
    for nome, tipo in (
        ("control.css", b"--accent"),
        ("control.js", b"localStorage"),
    ):
        status, corpo = pedir(base, "GET", f"/control/static/{nome}")
        assert status == 200
        assert tipo in corpo


def test_asset_fora_da_allowlist_e_404(setup):
    base, *_ = setup
    status, _ = pedir(base, "GET", "/control/static/../../etc/passwd")
    assert status in {404, 400}


def test_api_exige_token_do_painel(setup):
    base, *_ = setup
    assert pedir(base, "GET", "/control/api/overview")[0] == 401
    assert pedir(base, "GET", "/control/api/overview", token="tok-chat")[0] == 401
    assert pedir(base, "GET", "/control/api/overview", token="ctl-secreto")[0] == 200


def test_overview_traz_estado_real(setup):
    base, *_ = setup
    status, corpo = pedir(base, "GET", "/control/api/overview", token="ctl-secreto")
    assert status == 200
    assert corpo["engine"]["model"]
    assert corpo["status"] in {"ok", "warn", "error"}
    assert len(corpo["health"]) >= 6
    assert "skills" in corpo and corpo["skills"]["total"] >= 10
    assert "automations" in corpo
    assert set(corpo["credentials"]) == {"current", "configured", "missing_key"}


def test_health_e_skills(setup):
    base, *_ = setup
    status, corpo = pedir(base, "GET", "/control/api/health", token="ctl-secreto")
    assert status == 200
    assert {item["id"] for item in corpo["items"]} >= {"engine", "database", "scheduler"}

    status, corpo = pedir(base, "GET", "/control/api/skills", token="ctl-secreto")
    assert status == 200
    assert any(item["id"] == "career" for item in corpo["skills"])


def test_automations_e_runs(setup):
    base, _, store, _ = setup
    run = store.begin_run(kind="career", automation="career:weekly")
    store.finish_run(run, status="completed", detail="3 vagas")

    status, corpo = pedir(base, "GET", "/control/api/automations", token="ctl-secreto")
    assert status == 200
    carreira = next(a for a in corpo["automations"] if a["id"] == "career:weekly")
    assert carreira["last_run"]["detail"] == "3 vagas"
    assert "next_run" in carreira

    status, corpo = pedir(base, "GET", "/control/api/runs?limit=5", token="ctl-secreto")
    assert status == 200
    assert corpo["runs"][0]["kind"] == "career"


def test_filtro_de_runs_por_status(setup):
    base, _, store, _ = setup
    ok = store.begin_run(kind="career")
    store.finish_run(ok, status="completed")
    ruim = store.begin_run(kind="career")
    store.finish_run(ruim, status="failed", error="502 bad gateway")

    _, corpo = pedir(base, "GET", "/control/api/runs?status=failed", token="ctl-secreto")
    assert len(corpo["runs"]) == 1
    assert corpo["runs"][0]["status"] == "failed"


def test_errors_lista_falhas(setup):
    base, _, store, _ = setup
    ruina = store.begin_run(kind="market")
    store.finish_run(ruina, status="failed", error="timeout", error_kind="network")
    _, corpo = pedir(base, "GET", "/control/api/errors", token="ctl-secreto")
    assert len(corpo["errors"]) == 1
    assert corpo["errors"][0]["error_kind"] == "network"


def test_providers_nao_vaza_chave(setup):
    base, *_ = setup
    _, corpo = pedir(base, "GET", "/control/api/providers", token="ctl-secreto")
    texto = json.dumps(corpo)
    assert "sk-teste-12345678" not in texto
    nomes = {p["name"] for p in corpo["providers"]}
    assert "openai" in nomes


def test_secrets_mascarados(setup):
    base, *_ = setup
    _, corpo = pedir(base, "GET", "/control/api/secrets", token="ctl-secreto")
    item = next(s for s in corpo["secrets"] if s["key"] == "OPENAI_API_KEY")
    assert item["masked"] == "sk-…5678"
    assert "sk-teste" not in json.dumps(corpo)


def test_settings_traz_origem(setup):
    base, *_ = setup
    _, corpo = pedir(base, "GET", "/control/api/settings", token="ctl-secreto")
    secao = next(s for s in corpo["sections"] if s["section"] == "proactive")
    campo = next(c for c in secao["fields"] if c["key"] == "idle_minutes")
    assert campo["source"] in {"default", "toml", "env"}
    assert "api_key" not in json.dumps(corpo["sections"])


def test_patch_settings_grava_toml(setup):
    base, _, _, _ = setup
    status, corpo = pedir(
        base,
        "PATCH",
        "/control/api/settings",
        token="ctl-secreto",
        corpo={"section": "proactive", "key": "daily_max", "value": 6},
    )
    assert status == 200
    assert corpo["value"] == 6
    assert Path(corpo["path"]).read_text().count("daily_max = 6") == 1


def test_patch_settings_valor_invalido_422(setup):
    base, *_ = setup
    status, corpo = pedir(
        base,
        "PATCH",
        "/control/api/settings",
        token="ctl-secreto",
        corpo={"section": "proactive", "key": "daily_max", "value": -1},
    )
    assert status == 422
    assert "daily_max" in corpo["message"]


def test_patch_settings_campo_sensivel_422(setup):
    base, *_ = setup
    status, corpo = pedir(
        base,
        "PATCH",
        "/control/api/settings",
        token="ctl-secreto",
        corpo={"section": "agent", "key": "api_key", "value": "sk-x"},
    )
    assert status == 422
    assert "sensivel" in corpo["message"]


def test_put_secret_grava_e_mascara(setup):
    base, _, _, env = setup
    status, corpo = pedir(
        base,
        "PUT",
        "/control/api/secrets/DEEPSEEK_API_KEY",
        token="ctl-secreto",
        corpo={"value": "sk-nova-abcdef1234"},
    )
    assert status == 200
    assert corpo["masked"] == "sk-…1234"
    assert "sk-nova" not in json.dumps(corpo)
    assert "DEEPSEEK_API_KEY" in env.read_text()


def test_put_secret_fora_da_allowlist_422(setup):
    base, *_ = setup
    status, corpo = pedir(
        base,
        "PUT",
        "/control/api/secrets/LD_PRELOAD",
        token="ctl-secreto",
        corpo={"value": "/tmp/evil.so"},
    )
    assert status == 422
    assert "permitida" in corpo["message"]


def test_delete_secret_comenta(setup):
    base, _, _, env = setup
    status, corpo = pedir(
        base, "DELETE", "/control/api/secrets/OPENAI_API_KEY", token="ctl-secreto"
    )
    assert status == 200
    assert corpo["removed"] is True
    assert "# OPENAI_API_KEY" in env.read_text()


def test_sem_control_token_api_responde_503(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    store = MemoryStore(tmp_path / "gaia.db")
    centro = ControlCenter(cfg, store, env_path=tmp_path / ".env")
    servidor = ServeServer(
        FakeTarget(),  # type: ignore[arg-type]
        RemoteUI(),
        host="127.0.0.1",
        port=0,
        token="tok-chat",
        control_token="",
        center=centro,
    )
    base = servidor.start()
    try:
        status, _ = pedir(base, "GET", "/control/api/overview", token="tok-chat")
        assert status == 401
        status, corpo = pedir(base, "GET", "/control")
        assert status == 503
        assert b"GAIA_CONTROL_TOKEN" in corpo
    finally:
        servidor.shutdown()
        store.close()


def test_health_do_chat_continua_igual(setup):
    base, *_ = setup
    status, corpo = pedir(base, "GET", "/health")
    assert status == 200
    assert corpo["ok"] is True


# ---------------------------------------------------------------- sessao/cookie
def pedir_com_cookie(
    base: str,
    metodo: str,
    caminho: str,
    *,
    cookie: str | None = None,
    token: str | None = None,
    corpo: Any = None,
) -> tuple[int, Any, str]:
    """Como `pedir`, mas devolve tambem o Set-Cookie da resposta."""
    host, porta = base.removeprefix("http://").split(":")
    conexao = http.client.HTTPConnection(host, int(porta), timeout=10)
    cabecalhos: dict[str, str] = {}
    if token is not None:
        cabecalhos["Authorization"] = f"Bearer {token}"
    if cookie is not None:
        cabecalhos["Cookie"] = cookie
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        cabecalhos["Content-Type"] = "application/json"
    conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conexao.getresponse()
    bruto = resposta.read()
    set_cookie = resposta.getheader("Set-Cookie") or ""
    conexao.close()
    try:
        return resposta.status, json.loads(bruto), set_cookie
    except ValueError:
        return resposta.status, bruto, set_cookie


def _nonce(set_cookie: str) -> str:
    return set_cookie.split(";", 1)[0]


def test_session_emite_cookie_httponly(setup):
    base, *_ = setup
    status, corpo, set_cookie = pedir_com_cookie(
        base, "POST", "/control/api/session", token="ctl-secreto", corpo={}
    )
    assert status == 200
    assert corpo["ok"] is True
    assert "gaia_control=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Path=/control" in set_cookie
    # O nonce nunca e o token do painel.
    assert "ctl-secreto" not in set_cookie


def test_cookie_da_sessao_autoriza_api(setup):
    base, *_ = setup
    _, _, set_cookie = pedir_com_cookie(
        base, "POST", "/control/api/session", token="ctl-secreto", corpo={}
    )
    cookie = _nonce(set_cookie)
    status, corpo = pedir(base, "GET", "/control/api/overview")
    assert status == 401  # sem prova
    status, corpo, _ = pedir_com_cookie(
        base, "GET", "/control/api/overview", cookie=cookie
    )
    assert status == 200
    assert corpo["engine"]["model"]


def test_session_exige_token_do_painel(setup):
    base, *_ = setup
    # Sem token, com token errado e com o token do chat: nenhum emite cookie.
    for token in (None, "errado", "tok-chat"):
        status, _, set_cookie = pedir_com_cookie(
            base, "POST", "/control/api/session", token=token, corpo={}
        )
        assert status == 401
        assert set_cookie == ""


def test_cookie_inventado_nao_autoriza(setup):
    base, *_ = setup
    status, _, _ = pedir_com_cookie(
        base, "GET", "/control/api/overview", cookie="gaia_control=nao-existe"
    )
    assert status == 401
    # O token de chat continua sem poder administrativo, mesmo em cookie.
    assert pedir(base, "GET", "/control/api/overview", token="tok-chat")[0] == 401


def test_delete_session_revoga(setup):
    base, *_ = setup
    _, _, set_cookie = pedir_com_cookie(
        base, "POST", "/control/api/session", token="ctl-secreto", corpo={}
    )
    cookie = _nonce(set_cookie)
    status, corpo, limpo = pedir_com_cookie(
        base, "DELETE", "/control/api/session", cookie=cookie
    )
    assert status == 200
    assert corpo["ok"] is True
    assert "Max-Age=0" in limpo
    # O cookie antigo nao vale mais.
    status, _, _ = pedir_com_cookie(
        base, "GET", "/control/api/overview", cookie=cookie
    )
    assert status == 401


def test_get_session_diz_se_esta_autenticado(setup):
    base, *_ = setup
    status, corpo = pedir(base, "GET", "/control/api/session")
    assert status == 200
    assert corpo["authenticated"] is False
    _, _, set_cookie = pedir_com_cookie(
        base, "POST", "/control/api/session", token="ctl-secreto", corpo={}
    )
    _, corpo, _ = pedir_com_cookie(
        base, "GET", "/control/api/session", cookie=_nonce(set_cookie)
    )
    assert corpo["authenticated"] is True
    assert corpo["mode"] == "session"


def test_sem_control_token_session_responde_503(tmp_path, isolated_env):
    cfg = Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    store = MemoryStore(tmp_path / "gaia.db")
    centro = ControlCenter(cfg, store, env_path=tmp_path / ".env")
    servidor = ServeServer(
        FakeTarget(),  # type: ignore[arg-type]
        RemoteUI(),
        host="127.0.0.1",
        port=0,
        token="tok-chat",
        control_token="",
        center=centro,
    )
    base = servidor.start()
    try:
        status, corpo, set_cookie = pedir_com_cookie(
            base, "POST", "/control/api/session", token="tok-chat", corpo={}
        )
        assert status == 503
        assert set_cookie == ""
        assert "GAIA_CONTROL_TOKEN" in corpo["message"]
    finally:
        servidor.shutdown()
        store.close()
