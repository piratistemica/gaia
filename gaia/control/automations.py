"""Agenda declarada das automacoes: quando cada rotina deve rodar e o estado.

Este modulo **nao agenda e nao executa**. Ele le os mesmos campos de config que
os workers do ``Orchestrator`` usam e responde tres perguntas para o painel:

* quando e a proxima execucao (``next_run``);
* o que aconteceu na ultima (``last_run``, vindo de ``runs``);
* a automacao deveria ter rodado e nao rodou? (``missed``).

A distincao que o pedido exige — "aguardando o horario" versus "deveria ter
executado" — sai da comparacao entre a janela declarada, a reserva diaria em
``meta`` (``claim_meta``) e o historico em ``runs``. Nada aqui toca no
agendador: a Fase C substitui os gates por este modulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from gaia.briefing.market import market_due
from gaia.career.service import career_due
from gaia.config import Config
from gaia.memory.store import MemoryStore

# Tolerancia depois do fim da janela antes de considerar "perdida". Cobre o
# intervalo de checagem dos workers (30s) e o tempo de uma execucao longa.
MISSED_TOLERANCE_MINUTES = 20


@dataclass(frozen=True)
class Automation:
    """Uma rotina com horario declarado."""

    id: str
    skill: str
    name: str
    days: tuple[int, ...]  # 0 = segunda
    at: str  # HH:MM
    until: str  # HH:MM
    delivery: str  # telegram | local
    # Prefixo da chave de reserva diaria em ``meta`` (ex.: "career:").
    claim_prefix: str = ""
    # Slot extra para rotinas com mais de uma janela por dia (briefing).
    slot: str = ""
    enabled_path: str = ""

    def claim_key(self, day: datetime) -> str:
        """Chave de ``claim_meta`` equivalente a usada pela rotina real."""
        if not self.claim_prefix:
            return ""
        base = self.claim_prefix.format(day=day.date().isoformat())
        return f"{base}:{self.slot}" if self.slot else base

    def matches_day(self, when: datetime) -> bool:
        return when.weekday() in self.days

    def in_window(self, when: datetime) -> bool:
        hora = when.strftime("%H:%M")
        if self.at <= self.until:
            return self.at <= hora < self.until
        # Janela que vira a meia-noite (ex.: sonho 02:00-08:00 nao vira, mas
        # 23:00-01:00 viraria).
        return hora >= self.at or hora < self.until


def _enabled(cfg: Config, item: Automation) -> bool:
    """True se o caminho de config da automacao estiver ligado."""
    if not item.enabled_path:
        return True
    atual: Any = cfg
    for parte in item.enabled_path.split("."):
        atual = getattr(atual, parte, None)
        if atual is None:
            return False
    return bool(atual)


def describe(cfg: Config) -> list[Automation]:
    """Agenda declarada, derivada do config atual.

    A ordem segue a do dia real: briefing, carteira, vagas, memoria/sonho.
    """
    briefing = cfg.briefing
    career = cfg.career
    memory = cfg.memory
    itens: list[Automation] = [
        Automation(
            id="briefing:morning",
            skill="briefing",
            name="Briefing da manha",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=briefing.morning_at,
            until=briefing.noon_at,
            delivery="telegram",
            claim_prefix="briefing:{day}",
            slot="morning",
            enabled_path="briefing.enabled",
        ),
        Automation(
            id="briefing:noon",
            skill="briefing",
            name="Resumo do meio-dia",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=briefing.noon_at,
            until=briefing.noon_until,
            delivery="telegram",
            claim_prefix="briefing:{day}",
            slot="noon",
            enabled_path="briefing.enabled",
        ),
        Automation(
            id="briefing:evening",
            skill="briefing",
            name="Fecho da noite",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=briefing.evening_at,
            until=briefing.evening_until,
            delivery="telegram",
            claim_prefix="briefing:{day}",
            slot="evening",
            enabled_path="briefing.evening_enabled",
        ),
        Automation(
            id="market:watchlist",
            skill="market",
            name="Resumo da carteira",
            days=tuple(briefing.market_days),
            at=briefing.market_at,
            until=briefing.market_until,
            delivery="telegram",
            claim_prefix="briefing:market:{day}",
            enabled_path="briefing.market_enabled",
        ),
        Automation(
            id="career:weekly",
            skill="career",
            name="Pesquisa de vagas",
            days=tuple(career.days),
            at=career.at,
            until=career.until,
            delivery="telegram",
            claim_prefix="career:{day}",
            enabled_path="career.enabled",
        ),
        Automation(
            id="memory:daily",
            skill="memory",
            name="Consolidacao da memoria",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=memory.at,
            until="23:59",
            delivery="local",
            claim_prefix="memory:{day}",
            enabled_path="memory.enabled",
        ),
        Automation(
            id="memory:dream",
            skill="dream",
            name="Sonho noturno",
            days=(0, 1, 2, 3, 4, 5, 6),
            at=memory.dream_at,
            until=memory.dream_until,
            delivery="local",
            claim_prefix="dream:{day}",
            enabled_path="memory.dream_enabled",
        ),
    ]
    return itens


def find(cfg: Config, automation_id: str) -> Automation | None:
    for item in describe(cfg):
        if item.id == automation_id:
            return item
    return None


def next_run(item: Automation, now: datetime) -> datetime | None:
    """Proxima execucao prevista (None se a automacao nunca roda)."""
    if not item.days:
        return None
    for offset in range(0, 8):
        dia = (now + timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if not item.matches_day(dia):
            continue
        hora, minuto = _split(item.at)
        alvo = dia.replace(hour=hora, minute=minuto)
        if offset == 0 and alvo <= now:
            continue
        return alvo
    return None


def _split(value: str) -> tuple[int, int]:
    try:
        hora, _, minuto = (value or "").partition(":")
        return int(hora), int(minuto or 0)
    except ValueError:
        return 0, 0


def status(
    item: Automation,
    cfg: Config,
    store: MemoryStore | None,
    now: datetime | None = None,
) -> str:
    """scheduled | running | completed | failed | missed | disabled | paused.

    ``running`` tem prioridade sobre ``failed`` porque um run aberto significa
    que a rotina esta executando agora. ``missed`` so aparece quando a janela
    ja fechou (com tolerancia) e nao ha reserva nem execucao no dia.
    """
    momento = now or datetime.now().astimezone()
    if not _enabled(cfg, item):
        return "disabled"
    if not item.days:
        return "disabled"
    if store is not None:
        ultima = store.last_run(item.skill, item.id)
        if ultima is not None and str(ultima.get("status")) == "running":
            return "running"
    if item.matches_day(momento) and item.in_window(momento):
        return "scheduled"
    if item.matches_day(momento):
        hora_atual = momento.strftime("%H:%M")
        if hora_atual >= item.until:
            fim = momento.replace(hour=0, minute=0, second=0, microsecond=0)
            hora, minuto = _split(item.until)
            fim = fim.replace(hour=hora, minute=minuto)
            if momento - fim >= timedelta(minutes=MISSED_TOLERANCE_MINUTES):
                if not _ran_today(item, store, momento):
                    return "missed"
    if store is not None:
        ultima = store.last_run(item.skill, item.id)
        if ultima is not None:
            return str(ultima.get("status") or "scheduled")
    return "scheduled"


def _ran_today(
    item: Automation, store: MemoryStore | None, now: datetime
) -> bool:
    """A rotina entregou/executou hoje (reserva em meta ou run no dia)?"""
    if store is None:
        return False
    chave = item.claim_key(now)
    if chave and store.get_meta(chave):
        return True
    ultima = store.last_run(item.skill, item.id)
    if ultima is None:
        return False
    iniciado = str(ultima.get("started_at") or "")
    return iniciado.startswith(now.date().isoformat())


def describe_automations(
    cfg: Config,
    store: MemoryStore | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Lista pronta para o painel, com proxima execucao e estado."""
    momento = now or datetime.now().astimezone()
    itens: list[dict[str, Any]] = []
    for item in describe(cfg):
        proxima = next_run(item, momento)
        ultima = store.last_run(item.skill, item.id) if store is not None else None
        itens.append(
            {
                "id": item.id,
                "skill": item.skill,
                "name": item.name,
                "days": list(item.days),
                "days_label": _days_label(item.days),
                "at": item.at,
                "until": item.until,
                "delivery": item.delivery,
                "enabled": _enabled(cfg, item),
                "status": status(item, cfg, store, momento),
                "next_run": proxima.isoformat(timespec="seconds") if proxima else "",
                "last_run": (
                    {
                        "id": int(ultima.get("id") or 0),
                        "status": str(ultima.get("status") or ""),
                        "started_at": str(ultima.get("started_at") or ""),
                        "duration_ms": int(ultima.get("duration_ms") or 0),
                        "detail": str(ultima.get("detail") or ""),
                        "error": str(ultima.get("error") or ""),
                        "error_kind": str(ultima.get("error_kind") or ""),
                    }
                    if ultima is not None
                    else None
                ),
            }
        )
    return itens


_DAY_NAMES = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")


def _days_label(days: tuple[int, ...]) -> str:
    """Rotulo curto: "todos os dias", "seg, qua, sex" ou "nunca"."""
    if not days:
        return "nunca"
    if set(days) == set(range(7)):
        return "todos os dias"
    return ", ".join(_DAY_NAMES[day] for day in days if 0 <= day < 7)


def due_now(item: Automation, cfg: Config, now: datetime) -> bool:
    """A janela esta aberta agora (usado pela Fase C e pelos testes).

    Reaproveita as funcoes puras que os workers ja usam; nao reimplementa a
    regra de horario.
    """
    if not _enabled(cfg, item):
        return False
    hora = now.strftime("%H:%M")
    if item.skill == "career":
        return career_due(hora, item.at, item.until, now.weekday(), item.days)
    if item.skill == "market":
        return market_due(
            hora, item.at, item.until, now.weekday(), list(item.days)
        )
    return item.matches_day(now) and item.in_window(now)


def needs_attention(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Automacoes que merecem destaque no painel (perdida ou falhou)."""
    return [
        item
        for item in items
        if item["status"] in {"missed", "failed"}
        or (item["last_run"] or {}).get("status") == "failed"
    ]


__all__ = [
    "MISSED_TOLERANCE_MINUTES",
    "Automation",
    "describe",
    "describe_automations",
    "due_now",
    "find",
    "needs_attention",
    "next_run",
    "status",
]
