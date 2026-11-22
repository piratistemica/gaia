from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from gaia.agent.errors import ToolError
from gaia.agent.tools import ToolContext, build_tools
from gaia.agent.tools.estudos import build_estudos_tools
from gaia.config import EstudosConfig
from gaia.controller.initiative import InitiativeMixin
from gaia.estudos.client import EstudosClient, _load_env_file
from gaia.estudos.progresso import (
    formatar_resumo,
    resumo_semana,
    revisoes_vencidas,
    week_start,
)


# --------------------------------------------------------------- ambiente/cliente
def _env_estudos(tmp_path) -> str:
    env_file = tmp_path / "estudos.env"
    env_file.write_text(
        "VITE_SUPABASE_URL=https://abin.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=chave\n"
        "ESTUDOS_USER_ID=user-1\n",
        encoding="utf-8",
    )
    return str(env_file)


def test_env_file_e_cliente_habilitado(tmp_path):
    env_file = Path(_env_estudos(tmp_path))
    client = EstudosClient(EstudosConfig(env_file=str(env_file)))
    assert client.enabled is True
    assert _load_env_file(env_file)["ESTUDOS_USER_ID"] == "user-1"


def test_cliente_desabilitado_sem_env(tmp_path):
    client = EstudosClient(EstudosConfig(env_file=str(tmp_path / "nao.env")))
    assert client.enabled is False


def test_cliente_desabilitado_por_config(tmp_path):
    cfg = EstudosConfig(env_file=_env_estudos(tmp_path), enabled=False)
    assert EstudosClient(cfg).enabled is False


# --------------------------------------------------------------- progresso puro
def _result(finished: datetime, pct: float = 60.0, sec: int = 600) -> dict:
    return {
        "payload": {
            "finishedAt": finished.isoformat(),
            "pct": pct,
            "elapsedSec": sec,
        }
    }


def test_week_start_e_segunda_meia_noite():
    # quarta 18/11/2026 -> segunda 16/11/2026 00:00
    quarta = datetime(2026, 11, 18, 15, 0).astimezone()
    inicio = week_start(quarta)
    assert inicio.weekday() == 0  # segunda
    assert (inicio.hour, inicio.minute) == (0, 0)
    assert inicio.date() == datetime(2026, 11, 16).date()


def test_resumo_conta_so_a_semana_e_estudou_hoje():
    agora = datetime(2026, 11, 18, 21, 0).astimezone()  # quarta
    segunda = week_start(agora)
    results = [
        _result(agora),  # hoje
        _result(segunda + timedelta(hours=9), pct=40, sec=1200),  # semana, antes
        _result(segunda - timedelta(days=1), pct=99, sec=9999),  # semana passada
    ]
    goals = {"sessionsPerWeek": 4, "minutesPerWeek": 300, "targetPct": 60}
    resumo = resumo_semana(results, goals, now=agora)
    assert resumo.sessoes == 2
    assert resumo.minutos == 30  # (600 + 1200) / 60
    assert resumo.pct_medio == 50.0
    assert resumo.estudou_hoje is True
    assert resumo.atrasado is True  # 2 < 4 e 30 < 300


def test_resumo_meta_em_dia_nao_fica_atrasado():
    agora = datetime(2026, 11, 17, 10, 0).astimezone()  # terca
    results = [
        _result(agora, pct=70, sec=600),
        _result(agora, pct=80, sec=600),
    ]
    goals = {"sessionsPerWeek": 2, "minutesPerWeek": 20, "targetPct": 60}
    resumo = resumo_semana(results, goals, now=agora)
    assert resumo.sessoes == 2
    assert resumo.atrasado is False
    assert resumo.estudou_hoje is True


def test_resumo_sem_sessoes_fica_atrasado():
    agora = datetime(2026, 11, 18, 21, 0).astimezone()
    resumo = resumo_semana([], {"sessionsPerWeek": 3}, now=agora)
    assert resumo.sessoes == 0
    assert resumo.atrasado is True
    assert resumo.estudou_hoje is False


def test_sem_meta_configurada_nao_fica_atrasado():
    # App manda sessionsPerWeek=0 quando ninguem configurou meta.
    agora = datetime(2026, 11, 18, 21, 0).astimezone()
    resumo = resumo_semana(
        [], {"sessionsPerWeek": 0, "minutesPerWeek": 0, "targetPct": 60}, now=agora
    )
    assert resumo.sessoes == 0
    assert resumo.atrasado is False


def test_revisoes_vencidas_usa_dueatim_ms():
    agora = datetime(2026, 11, 18, 12, 0).astimezone()
    agora_ms = agora.timestamp() * 1000
    rows = [
        {"payload": {"dueAt": agora_ms - 1000}},  # vencida
        {"payload": {"dueAt": agora_ms + 10_000_000}},  # futura
        {"payload": {}},  # sem dueAt
    ]
    assert revisoes_vencidas(rows, now=agora) == 1


def _result_utc(quando_local: datetime, pct: float = 60.0, sec: int = 600) -> dict:
    """Mesmo resultado, mas com ``finishedAt`` em UTC — o formato do app."""
    return {
        "payload": {
            "finishedAt": quando_local.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "pct": pct,
            "elapsedSec": sec,
        }
    }


def test_estudou_hoje_compara_no_fuso_local_e_nao_em_utc():
    # Estudou 23:30 local; no fuso UTC isso ja e dia seguinte.
    agora = datetime(2026, 11, 18, 23, 59).astimezone()
    row = _result_utc(datetime(2026, 11, 18, 23, 30).astimezone())
    resumo = resumo_semana([row], {"sessionsPerWeek": 4}, now=agora)
    assert resumo.estudou_hoje is True


def test_estudo_de_ontem_a_noite_nao_conta_como_hoje():
    agora = datetime(2026, 11, 19, 20, 0).astimezone()  # quinta 20h
    row = _result_utc(datetime(2026, 11, 18, 22, 30).astimezone())  # quarta 22:30
    resumo = resumo_semana([row], {"sessionsPerWeek": 4}, now=agora)
    assert resumo.estudou_hoje is False
    assert resumo.sessoes == 1  # continua valendo para a semana


def test_formatar_resumo_inclui_nota_e_meta():
    agora = datetime(2026, 11, 18, 21, 0).astimezone()
    resumo = resumo_semana(
        [_result(agora, pct=55.5, sec=600)],
        {"sessionsPerWeek": 4, "minutesPerWeek": 120, "targetPct": 60},
        now=agora,
    )
    texto = formatar_resumo(resumo)
    assert "1/4 simulados" in texto
    assert "10/120 min" in texto
    assert "nota média 55.5%" in texto


# --------------------------------------------------------------- ferramentas
class _FakeClient:
    enabled = True
    results_rows: ClassVar[list[dict]] = []
    goals_row: ClassVar[dict] = {}
    review_rows: ClassVar[list[dict]] = []

    def __init__(self, cfg):
        self.cfg = cfg

    async def results(self):
        return list(self.results_rows)

    async def goals(self):
        return dict(self.goals_row)

    async def reviews(self):
        return list(self.review_rows)


@pytest.fixture
def tools_estudos(tmp_path, monkeypatch):
    import gaia.agent.tools.estudos as modulo

    monkeypatch.setattr(modulo, "EstudosClient", _FakeClient)
    agora = datetime.now().astimezone()
    _FakeClient.results_rows = [
        {
            "payload": {
                "finishedAt": agora.isoformat(),
                "pct": 66.0,
                "elapsedSec": 900,
            }
        }
    ]
    _FakeClient.goals_row = {"sessionsPerWeek": 3, "minutesPerWeek": 90, "targetPct": 60}
    _FakeClient.review_rows = [{"payload": {"dueAt": 1_000_000_000_000}}]
    cfg = EstudosConfig(env_file=_env_estudos(tmp_path))
    return {tool.name: tool for tool in build_estudos_tools()}, ToolContext(
        tmp_path, True, estudos=cfg
    )


def test_tools_de_estudos_so_existem_com_credencial(tmp_path):
    sem = build_tools(ToolContext(Path(".")))
    assert "estudos_progresso" not in sem.names()

    cfg = EstudosConfig(env_file=_env_estudos(tmp_path))
    com = build_tools(ToolContext(Path("."), True, None, estudos=cfg))
    assert {"estudos_progresso", "estudos_metas", "estudos_pendentes"} <= set(com.names())
    assert com.get("estudos_progresso").requires_approval is False


def test_estudos_progresso_descreve_a_semana(tools_estudos):
    tools, ctx = tools_estudos
    saida = tools["estudos_progresso"].handler({}, ctx)
    assert saida.ok
    assert "1/3 simulados" in saida.content
    assert "estudou hoje" in saida.content
    assert "nota média 66.0%" in saida.content


def test_estudos_metas_lista_objetivos(tools_estudos):
    tools, ctx = tools_estudos
    saida = tools["estudos_metas"].handler({}, ctx)
    assert saida.ok
    assert "3 simulados por semana" in saida.content
    assert "90 minutos por semana" in saida.content
    assert "nota alvo 60%" in saida.content


def test_estudos_pendentes_conta_revisoes(tools_estudos):
    tools, ctx = tools_estudos
    saida = tools["estudos_pendentes"].handler({}, ctx)
    assert saida.ok
    assert "1 item(ns) para revisar hoje" in saida.content


def test_estudos_tool_sem_credencial_avisa(tmp_path):
    from gaia.agent.tools.estudos import estudos_progresso

    ctx = ToolContext(tmp_path, True, None, estudos=EstudosConfig(env_file=str(tmp_path / "no.env")))
    with pytest.raises(ToolError, match="Estudos"):
        estudos_progresso({}, ctx)


# --------------------------------------------------------------- proatividade
NOW_LATE = datetime(2026, 11, 18, 21, 0).astimezone()  # quarta, 21h


class _MetaStore:
    """Store minimo para claim_meta/set_meta/delete_meta."""

    def __init__(self):
        self.dados: dict[str, str] = {}

    def claim_meta(self, key: str, value: str) -> bool:
        if key in self.dados:
            return False
        self.dados[key] = value
        return True

    def set_meta(self, key: str, value: str) -> None:
        self.dados[key] = value

    def delete_meta(self, key: str) -> None:
        self.dados.pop(key, None)


class _Estudos:
    enabled = True

    def __init__(self, results: list[dict], goals: dict, *, error: bool = False):
        self._results = results
        self._goals = goals
        self._error = error

    async def results(self):
        if self._error:
            from gaia.estudos.client import EstudosError

            raise EstudosError("fora do ar")
        return list(self._results)

    async def goals(self):
        return dict(self._goals)


class _Stub:
    def __init__(
        self,
        estudos: _Estudos,
        *,
        remind_at: str = "20:00",
        remind_when_late: bool = True,
        study_days: list[int] | None = None,
    ):
        self.cfg = SimpleNamespace(
            estudos=SimpleNamespace(
                enabled=True,
                remind_when_late=remind_when_late,
                remind_at=remind_at,
                study_days=[6, 2, 4, 5] if study_days is None else study_days,
                poll_minutes=60.0,
            )
        )
        self.estudos = estudos
        self.logger = logging.getLogger("test.estudos")
        self.store = _MetaStore()
        self._estudos_next_check = 0.0
        self.enviados: list[str] = []

    async def _say_proactive(self, text: str, count: bool = True) -> None:
        self.enviados.append(text)

    _estudos_reminder = InitiativeMixin._estudos_reminder


def _run_check(stub: _Stub, now: datetime = NOW_LATE) -> None:
    import asyncio

    asyncio.run(InitiativeMixin._check_estudos(stub, now))  # type: ignore[arg-type]


def test_cobra_quando_atrasado_e_sem_estudo_hoje():
    agora = NOW_LATE
    segunda = week_start(agora)
    estudos = _Estudos(
        [_result(segunda - timedelta(days=2))],  # nada nesta semana
        {"sessionsPerWeek": 4},
    )
    stub = _Stub(estudos)
    _run_check(stub, agora)
    assert len(stub.enviados) == 1
    assert "dia de estudo" in stub.enviados[0]
    assert "nenhum simulado nesta semana" in stub.enviados[0]
    assert stub.store.dados  # marcou o dia


def test_nao_cobra_em_dia_sem_estudo():
    # terca (weekday 1) nao esta nos dias de estudo padrao dom/qua/sex/sab
    terca = datetime(2026, 11, 17, 21, 0).astimezone()
    estudos = _Estudos([], {"sessionsPerWeek": 4})
    stub = _Stub(estudos)
    _run_check(stub, terca)
    assert stub.enviados == []


def test_nao_cobra_antes_do_horario():
    estudos = _Estudos([], {"sessionsPerWeek": 4})
    stub = _Stub(estudos, remind_at="23:00")
    _run_check(stub, NOW_LATE)
    assert stub.enviados == []


def test_nao_cobra_se_ja_estudou_hoje():
    agora = NOW_LATE
    estudos = _Estudos([_result(agora, pct=70)], {"sessionsPerWeek": 4})
    stub = _Stub(estudos)
    _run_check(stub, agora)
    assert stub.enviados == []


def test_nao_cobra_duas_vezes_no_mesmo_dia():
    estudos = _Estudos([], {"sessionsPerWeek": 4})
    stub = _Stub(estudos)
    _run_check(stub, NOW_LATE)
    _run_check(stub, NOW_LATE)
    assert len(stub.enviados) == 1


def test_nao_cobra_se_meta_em_dia():
    agora = NOW_LATE
    segunda = week_start(agora)
    resultados = [_result(segunda + timedelta(hours=8)) for _ in range(2)]
    estudos = _Estudos(resultados, {"sessionsPerWeek": 2})
    stub = _Stub(estudos)
    _run_check(stub, agora)
    assert stub.enviados == []


def test_falha_de_rede_nao_quebra_e_nao_marca():
    estudos = _Estudos([], {"sessionsPerWeek": 4}, error=True)
    stub = _Stub(estudos)
    _run_check(stub, NOW_LATE)
    assert stub.enviados == []
    assert stub.store.dados == {}
