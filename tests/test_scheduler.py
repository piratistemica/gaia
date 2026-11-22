"""Agendador unico (fase C): a tabela manda, o worker obedece.

Estes testes cobrem o que o pedido exige provar:

* uma automacao pausada nao executa de verdade (nem registra run);
* execucao manual gera run com ``trigger="manual"``;
* falha de rede e tentada de novo com backoff e a de auth nao;
* notificacao de falha no Telegram sai uma vez, nunca por tentativa.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from gaia.control import automations as automations_mod
from gaia.control.automations import Automation, seed
from gaia.controller.scheduler import SchedulerMixin
from gaia.memory.store import MemoryStore

# 23/11/2026 e segunda-feira.
SEGUNDA = datetime(2026, 11, 23, 9, 5).astimezone()


class FakeTelegram:
    enabled = True

    def __init__(self, falhar: bool = False) -> None:
        self.enviados: list[str] = []
        self.falhar = falhar

    async def send_message(self, text: str) -> None:
        if self.falhar:
            raise RuntimeError("502 bad gateway")
        self.enviados.append(text)


class _Agendador(SchedulerMixin):
    """Orchestrator minimo: so o estado que o agendador usa."""

    def __init__(self, cfg, store, telegram) -> None:
        import logging

        self.cfg = cfg
        self.store = store
        self.telegram = telegram
        self.logger = logging.getLogger("test.scheduler")
        self._busy = False
        self._recording = False
        self._listening = False
        self._scheduler_task = None
        self._retry_plan = {}
        self._career_last_attempt = 0.0
        self._briefing_last_attempt = 0.0
        self._market_last_attempt = 0.0
        self.chamadas: list[tuple[str, str, int]] = []
        self.falhar: dict[str, tuple[bool, str]] = {}

    async def _run_handler(self, item, now, trigger, attempt):
        self.chamadas.append((item.id, trigger, attempt))
        falhar, motivo = self.falhar.get(item.id, (False, ""))
        if falhar:
            kind = "auth" if "401" in motivo else "network"
            run = self.store.begin_run(
                kind=item.skill,
                automation=item.id,
                trigger=trigger,
                delivery=item.delivery,
                attempt=attempt,
            )
            self.store.finish_run(
                run, status="failed", error=motivo, error_kind=kind
            )
            return False
        run = self.store.begin_run(
            kind=item.skill,
            automation=item.id,
            trigger=trigger,
            delivery=item.delivery,
            attempt=attempt,
        )
        self.store.finish_run(run, status="completed", detail="ok")
        return True

    async def _automation_career(self, item, now, *, trigger="schedule", attempt=1):
        return await self._run_handler(item, now, trigger, attempt)

    async def _automation_market(self, item, now, *, trigger="schedule", attempt=1):
        return await self._run_handler(item, now, trigger, attempt)

    async def _automation_briefing(self, item, now, *, trigger="schedule", attempt=1):
        return await self._run_handler(item, now, trigger, attempt)

    async def _automation_evening(self, item, now, *, trigger="schedule", attempt=1):
        return await self._run_handler(item, now, trigger, attempt)

    async def _automation_memory(self, item, now, *, trigger="schedule", attempt=1):
        return await self._run_handler(item, now, trigger, attempt)

    async def _automation_dream(self, item, now, *, trigger="schedule", attempt=1):
        return await self._run_handler(item, now, trigger, attempt)


def _concrete(cls):
    """Versao concreta do mixin: os metodos cruzados viram no-ops."""
    stubs = {nome: (lambda *a, **k: None) for nome in cls.__abstractmethods__}
    return type(f"Fake{cls.__name__}", (cls,), stubs)


FakeOrchestrator = _concrete(_Agendador)


@pytest.fixture
def store(tmp_path):
    instance = MemoryStore(tmp_path / "gaia.db")
    yield instance
    instance.close()


@pytest.fixture
def cfg(tmp_path, isolated_env):
    from gaia.config import Config

    carregado = Config.load(
        config_path=tmp_path / "config.toml",
        env={"GAIA_DATA_DIR": str(tmp_path / "data")},
    )
    carregado.telegram.enabled = True
    carregado.telegram.bot_token = "1:abc"
    carregado.career.days = [0]
    carregado.career.at = "09:00"
    carregado.career.until = "18:00"
    carregado.briefing.market_days = [0]
    carregado.briefing.market_at = "12:00"
    carregado.briefing.market_until = "18:00"
    carregado.briefing.morning_at = "00:00"
    carregado.briefing.noon_at = "23:00"
    carregado.briefing.noon_until = "23:30"
    return carregado


@pytest.fixture
def orch(cfg, store):
    return FakeOrchestrator(cfg, store, FakeTelegram())


@pytest.mark.asyncio
async def test_start_scheduler_semeia_a_tabela_e_liga_o_laco(orch):
    orch.start_scheduler()
    assert orch._scheduler_task is not None
    assert len(orch.store.list_automations()) == 7
    orch._scheduler_task.cancel()


async def test_tick_executa_a_automacao_devida(orch):
    seed(orch.cfg, orch.store)

    await orch._scheduler_tick(SEGUNDA)

    assert ("career:weekly", "schedule", 1) in orch.chamadas
    runs = orch.store.list_runs(kind="career")
    assert runs and runs[0]["status"] == "completed"
    assert runs[0]["trigger"] == "schedule"


async def test_tick_nao_repete_a_mesma_janela(orch):
    seed(orch.cfg, orch.store)

    await orch._scheduler_tick(SEGUNDA)
    await orch._scheduler_tick(SEGUNDA + timedelta(minutes=1))

    carreiras = [c for c in orch.chamadas if c[0] == "career:weekly"]
    assert len(carreiras) == 1


async def test_automacao_pausada_nao_executa(orch):
    seed(orch.cfg, orch.store)
    orch.store.update_automation("career:weekly", enabled=False)

    await orch._scheduler_tick(SEGUNDA)

    assert "career:weekly" not in [c[0] for c in orch.chamadas]
    assert orch.store.list_runs(kind="career") == []


async def test_motor_ocupado_nao_dispara(orch):
    seed(orch.cfg, orch.store)
    orch._busy = True

    await orch._scheduler_tick(SEGUNDA)

    assert orch.chamadas == []
    # A janela nao foi reservada: o proximo tick ocioso ainda pode disparar.
    assert automations_mod.due(
        automations_mod.find_loaded(orch.cfg, orch.store, "career:weekly"),
        orch.cfg,
        SEGUNDA,
        orch.store,
    )


async def test_execucao_manual_gera_run_com_trigger_manual(orch):
    seed(orch.cfg, orch.store)

    resultado = await orch.run_automation_now("career:weekly")

    assert resultado["trigger"] == "manual"
    assert resultado["status"] == "completed"
    run = orch.store.list_runs(kind="career")[0]
    assert run["trigger"] == "manual"


async def test_execucao_manual_ignora_reserva_do_dia(orch):
    """Pedido explicito vale mais que 'ja rodou hoje'."""
    seed(orch.cfg, orch.store)
    hoje = datetime.now().astimezone().date().isoformat()
    orch.store.set_meta(f"career:{hoje}", "enviado")

    await orch.run_automation_now("career:weekly")

    # O agendador limpou a reserva da rotina antes de chamar; sem isso o
    # `_send_career` real devolveria False na hora ("ja rodou hoje").
    assert orch.store.get_meta(f"career:{hoje}") is None


async def test_execucao_manual_recusa_motor_ocupado(orch):
    seed(orch.cfg, orch.store)
    orch._busy = True

    with pytest.raises(RuntimeError, match="ocupado"):
        await orch.run_automation_now("career:weekly")


async def test_execucao_manual_recusa_id_desconhecido(orch):
    with pytest.raises(KeyError):
        await orch.run_automation_now("nao:existe")


async def test_falha_de_rede_agenda_retry_e_avisa(orch):
    seed(orch.cfg, orch.store)
    orch.falhar["career:weekly"] = (True, "502 bad gateway")

    await orch._scheduler_tick(SEGUNDA)

    assert "career:weekly" in orch._retry_plan
    _prazo, tentativa = orch._retry_plan["career:weekly"]
    assert tentativa == 2
    # O aviso sai ja na primeira falha (uma por falha, nunca por tentativa).
    assert len(orch.telegram.enviados) == 1
    assert "falhou" in orch.telegram.enviados[0]


async def test_falha_de_auth_nao_agenda_retry(orch):
    seed(orch.cfg, orch.store)
    orch.falhar["career:weekly"] = (True, "401 unauthorized")

    await orch._scheduler_tick(SEGUNDA)

    assert orch._retry_plan == {}
    assert len(orch.telegram.enviados) == 1


async def test_retry_respeita_o_backoff(orch, monkeypatch):
    seed(orch.cfg, orch.store)
    orch.falhar["career:weekly"] = (True, "timeout na conexao")
    await orch._scheduler_tick(SEGUNDA)

    # Antes do prazo: nada acontece.
    await orch._scheduler_tick(SEGUNDA)
    assert [c for c in orch.chamadas if c[1] == "retry"] == []

    # Depois do prazo: a segunda tentativa roda.
    prazo, tentativa = orch._retry_plan["career:weekly"]
    monkeypatch.setattr(
        "gaia.controller.scheduler.time.monotonic", lambda: prazo + 1
    )
    orch.falhar.pop("career:weekly")
    await orch._scheduler_tick(SEGUNDA + timedelta(minutes=30))

    retries = [c for c in orch.chamadas if c[1] == "retry"]
    assert retries == [("career:weekly", "retry", tentativa)]
    assert "career:weekly" not in orch._retry_plan


async def test_retry_para_no_teto(orch, monkeypatch):
    seed(orch.cfg, orch.store)
    orch.store.update_automation("career:weekly", retry_max=1, retry_backoff_minutes=0)
    orch.falhar["career:weekly"] = (True, "connection reset")

    await orch._scheduler_tick(SEGUNDA)
    prazo, _tentativa = orch._retry_plan["career:weekly"]
    monkeypatch.setattr(
        "gaia.controller.scheduler.time.monotonic", lambda: prazo + 1
    )
    await orch._scheduler_tick(SEGUNDA + timedelta(minutes=10))

    assert orch._retry_plan == {}
    # Uma mensagem por falha, nao por tentativa.
    assert len(orch.telegram.enviados) == 1


async def test_falha_local_nao_avisa_no_telegram(orch):
    seed(orch.cfg, orch.store)
    orch.cfg.memory.at = "23:00"
    orch.cfg.briefing.morning_at = "23:58"
    orch.cfg.briefing.market_at = "23:58"
    orch.cfg.career.at = "23:58"
    orch.cfg.briefing.evening_at = "23:58"
    orch.falhar["memory:daily"] = (True, "connection reset")
    # memory:daily e local: a janela vai das 23:00 as 23:59.

    noite = datetime(2026, 11, 23, 23, 30).astimezone()
    await orch._scheduler_tick(noite)

    runs = orch.store.list_runs(kind="memory")
    assert runs and runs[0]["status"] == "failed"
    assert orch.telegram.enviados == []


async def test_aviso_de_falha_nao_repete_apos_restart(orch):
    seed(orch.cfg, orch.store)
    orch.falhar["career:weekly"] = (True, "401 unauthorized")
    await orch._scheduler_tick(SEGUNDA)
    assert len(orch.telegram.enviados) == 1

    # "Restart": novo orquestrador, mesmo banco. A chave ja reservada impede
    # o segundo aviso da mesma falha.
    novo = FakeOrchestrator(orch.cfg, orch.store, orch.telegram)
    novo.falhar["career:weekly"] = (True, "401 unauthorized")
    await novo._scheduler_tick(SEGUNDA)
    assert len(orch.telegram.enviados) == 1


async def test_handler_ausente_devolve_a_janela(orch, monkeypatch):
    seed(orch.cfg, orch.store)
    item = automations_mod.find_loaded(orch.cfg, orch.store, "career:weekly")
    assert item is not None
    monkeypatch.setattr(
        "gaia.controller.scheduler._AUTOMATION_METHODS", {"career:weekly": "_nao_existe"}
    )

    await orch._run_automation(item, SEGUNDA)

    assert orch.store.get_meta(item.window_key(SEGUNDA)) is None


async def test_delivery_local_nao_manda_mensagem(orch):
    seed(orch.cfg, orch.store)
    item = Automation(
        id="x", skill="memory", name="X", days=(0,), at="00:00", until="23:59",
        delivery="local",
    )
    await orch._notify_automation_failure(
        item, {"status": "failed", "error": "boom", "error_kind": "network"}, SEGUNDA,
        final=True,
    )
    assert orch.telegram.enviados == []
