from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from zaya.controller.initiative import InitiativeMixin

NOW = datetime(2026, 11, 16, 20, 0)


class _Agenda:
    enabled = True

    def __init__(self, rows: list[dict]):
        self.rows = rows

    async def pending_today(self, date: str | None = None) -> list[dict]:
        return list(self.rows)


class _Stub:
    """Só o que `_check_agenda` toca; evita montar o orquestrador inteiro."""

    def __init__(self, rows: list[dict], notify: bool = True):
        self.cfg = SimpleNamespace(
            agenda=SimpleNamespace(
                enabled=True, poll_minutes=30.0, max_items=5, notify_pending=notify
            )
        )
        self.agenda = _Agenda(rows)
        self.logger = logging.getLogger("test.initiative")
        self._agenda_next_check = 0.0
        self._agenda_seen: set[str] = set()
        self._agenda_day = None
        self._agenda_pending_names: list[str] = []
        self.enviados: list[str] = []

    async def _say_proactive(self, text: str, count: bool = True) -> None:
        self.enviados.append(text)


def _row(ident: str, text: str) -> dict:
    return {"id": ident, "text": text, "is_checkbox": True, "completed": False}


async def _check(stub: _Stub) -> None:
    await InitiativeMixin._check_agenda(stub, NOW)  # type: ignore[arg-type]


def test_aviso_de_agenda_ignora_nota_sem_texto():
    stub = _Stub(
        [
            _row("a", "fio dental"),
            _row("b", ""),
            _row("c", "   "),
            _row("d", "falar com pacientes"),
        ]
    )
    import asyncio

    asyncio.run(_check(stub))

    assert len(stub.enviados) == 1
    aviso = stub.enviados[0]
    assert "fio dental" in aviso
    assert "falar com pacientes" in aviso
    assert ": fio dental, falar com pacientes." in aviso
    assert aviso.count("tarefa") == 1  # só o "tarefa(s)" do plural
    assert stub._agenda_pending_names == ["fio dental", "falar com pacientes"]


@pytest.mark.parametrize("notify", [False])
def test_aviso_de_agenda_respeita_notify_pending(notify: bool):
    stub = _Stub([_row("a", "pilates")], notify=notify)
    import asyncio

    asyncio.run(_check(stub))
    assert stub.enviados == []
    assert stub._agenda_pending_names == ["pilates"]
