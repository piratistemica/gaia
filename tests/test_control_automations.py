"""Testes da agenda declarada de automacoes (Fase A: so observacao)."""

from __future__ import annotations

from datetime import datetime

import pytest

from gaia.config import Config
from gaia.control.automations import (
    MISSED_TOLERANCE_MINUTES,
    describe,
    describe_automations,
    due_now,
    find,
    needs_attention,
    next_run,
    status,
)
from gaia.memory.store import MemoryStore

# 23/11/2026 e uma segunda-feira (0 = segunda).
SEGUNDA = datetime(2026, 11, 23, 8, 0).astimezone()


@pytest.fixture
def store(tmp_path):
    instance = MemoryStore(tmp_path / "gaia.db")
    yield instance
    instance.close()


@pytest.fixture
def cfg(tmp_path, isolated_env):
    return Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )


def _item(cfg, automation_id):
    achado = find(cfg, automation_id)
    assert achado is not None
    return achado


def test_describe_traz_as_automacoes_do_config(cfg):
    ids = [item.id for item in describe(cfg)]
    assert ids == [
        "briefing:morning",
        "briefing:noon",
        "briefing:evening",
        "market:watchlist",
        "career:weekly",
        "memory:daily",
        "memory:dream",
    ]


def test_describe_usa_os_horarios_do_config(cfg):
    cfg.career.at = "07:30"
    cfg.career.days = [0, 3]
    item = _item(cfg, "career:weekly")
    assert item.at == "07:30"
    assert item.days == (0, 3)
    assert item.delivery == "telegram"


def test_claim_key_bate_com_a_rotina_real(cfg):
    assert _item(cfg, "career:weekly").claim_key(SEGUNDA) == "career:2026-11-23"
    assert (
        _item(cfg, "briefing:morning").claim_key(SEGUNDA)
        == "briefing:2026-11-23:morning"
    )
    assert (
        _item(cfg, "market:watchlist").claim_key(SEGUNDA)
        == "briefing:market:2026-11-23"
    )


def test_next_run_na_segunda_de_manha(cfg):
    cfg.career.days = [0]
    cfg.career.at = "09:00"
    item = _item(cfg, "career:weekly")
    proxima = next_run(item, SEGUNDA)
    assert proxima is not None
    assert proxima.date() == SEGUNDA.date()
    assert proxima.strftime("%H:%M") == "09:00"


def test_next_run_pula_para_a_proxima_semana(cfg):
    cfg.career.days = [0]
    cfg.career.at = "09:00"
    item = _item(cfg, "career:weekly")
    depois = datetime(2026, 11, 23, 10, 0).astimezone()
    proxima = next_run(item, depois)
    assert proxima is not None
    assert proxima.date() == datetime(2026, 11, 30).date()


def test_next_run_sem_dias(cfg):
    cfg.briefing.market_days = []
    item = _item(cfg, "market:watchlist")
    assert next_run(item, SEGUNDA) is None


def test_status_disabled(cfg, store):
    cfg.career.enabled = False
    item = _item(cfg, "career:weekly")
    assert status(item, cfg, store, SEGUNDA) == "disabled"


def test_status_scheduled_dentro_da_janela(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "18:00"
    item = _item(cfg, "career:weekly")
    assert status(item, cfg, store, SEGUNDA) == "scheduled"


def test_status_missed_quando_janela_fechou_sem_reserva(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    assert status(item, cfg, store, depois) == "missed"


def test_status_nao_e_missed_com_reserva_em_meta(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    store.set_meta("career:2026-11-23", "enviado")
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    assert status(item, cfg, store, depois) != "missed"


def test_status_nao_e_missed_com_run_do_dia(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    run = store.begin_run(
        kind="career",
        automation="career:weekly",
        started_at="2026-11-23T08:05:00-03:00",
    )
    store.finish_run(run, status="failed", error="boom")
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    # Rodou (mesmo falhando): nao e "perdida", e falha registrada.
    assert status(item, cfg, store, depois) != "missed"


def test_status_missed_respeita_tolerancia(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    item = _item(cfg, "career:weekly")
    logo_depois = datetime(2026, 11, 23, 9, 1).astimezone()
    assert status(item, cfg, store, logo_depois) != "missed"
    bem_depois = datetime(2026, 11, 23, 9, 0 + MISSED_TOLERANCE_MINUTES + 1).astimezone()
    assert status(item, cfg, store, bem_depois) == "missed"


def test_status_running_tem_prioridade(cfg, store):
    item = _item(cfg, "career:weekly")
    store.begin_run(kind="career", automation="career:weekly")
    assert status(item, cfg, store, SEGUNDA) == "running"


def test_status_reflete_ultima_execucao(cfg, store):
    item = _item(cfg, "career:weekly")
    run = store.begin_run(kind="career", automation="career:weekly")
    store.finish_run(run, status="completed")
    depois = datetime(2026, 11, 25, 12, 0).astimezone()
    assert status(item, cfg, store, depois) == "completed"


def test_describe_automations_achata_tudo(cfg, store):
    itens = {item["id"]: item for item in describe_automations(cfg, store, SEGUNDA)}
    assert itens["career:weekly"]["enabled"] is True
    assert itens["career:weekly"]["delivery"] == "telegram"
    assert itens["career:weekly"]["days_label"] == "seg"
    assert itens["career:weekly"]["last_run"] is None
    assert itens["briefing:morning"]["days_label"] == "todos os dias"


def test_describe_automations_traz_ultima_execucao(cfg, store):
    run = store.begin_run(kind="career", automation="career:weekly")
    store.finish_run(run, status="failed", error="502", error_kind="network")
    itens = {item["id"]: item for item in describe_automations(cfg, store, SEGUNDA)}
    assert itens["career:weekly"]["last_run"]["status"] == "failed"
    assert itens["career:weekly"]["last_run"]["error_kind"] == "network"


def test_due_now_usa_a_regra_do_worker(cfg):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "18:00"
    item = _item(cfg, "career:weekly")
    assert due_now(item, cfg, SEGUNDA) is True
    assert due_now(item, cfg, datetime(2026, 11, 22, 12, 0).astimezone()) is False


def test_due_now_desligada(cfg):
    cfg.career.enabled = False
    item = _item(cfg, "career:weekly")
    assert due_now(item, cfg, SEGUNDA) is False


def test_needs_attention_pega_missed_e_falha(cfg, store):
    cfg.career.days = [0]
    cfg.career.at = "08:00"
    cfg.career.until = "09:00"
    depois = datetime(2026, 11, 23, 12, 0).astimezone()
    itens = describe_automations(cfg, store, depois)
    ids = {item["id"] for item in needs_attention(itens)}
    assert "career:weekly" in ids
