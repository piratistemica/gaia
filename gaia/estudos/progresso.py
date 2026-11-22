"""Calculo do progresso de estudos, sem rede.

Espelha a regra do Abin Suite (``assets/app.js``): a "semana" comeca na segunda
00:00 local e a meta semanal compara sessoes, minutos e nota liquida media
contra ``sessionsPerWeek``, ``minutesPerWeek`` e ``targetPct``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any


def parse_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Payload jsonb de uma linha de ``study_items`` (ou {} se nao for dict)."""
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _local(value: datetime) -> datetime:
    """Datas do navegador chegam com fuso; o resto assume o fuso local."""
    return value if value.tzinfo is not None else value.astimezone()


def week_start(now: datetime) -> datetime:
    """Segunda-feira 00:00 local, mesma regra do ``weekResults()`` do app."""
    local = _local(now)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, time.min, tzinfo=local.tzinfo)


def _finished_at(result: dict[str, Any]) -> datetime | None:
    """``finishedAt`` do app (``toISOString()``, sempre UTC) no fuso local.

    O app grava em UTC; comparar ``.date()`` com a data local sem converter
    joga o simulado da noite para o dia seguinte (e o da noite anterior para hoje).
    """
    raw = str(result.get("finishedAt") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


@dataclass(frozen=True)
class Resumo:
    sessoes: int
    meta_sessoes: int
    minutos: int
    meta_minutos: int
    pct_medio: float
    meta_pct: float
    estudou_hoje: bool
    atrasado: bool


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def resumo_semana(
    results: list[dict[str, Any]],
    goals: dict[str, Any],
    *,
    now: datetime,
) -> Resumo:
    """Resumo da semana corrente a partir dos payloads de ``results``/``goals``.

    ``atrasado`` so existe quando ha meta configurada (``sessionsPerWeek`` ou
    ``minutesPerWeek``); sem meta, nada a cobrar.
    """
    inicio = week_start(now)
    hoje = _local(now).date()

    da_semana: list[dict[str, Any]] = []
    estudou_hoje = False
    for row in results:
        payload = parse_payload(row)
        done = _finished_at(payload)
        if done is None:
            continue
        if done.date() == hoje:
            estudou_hoje = True
        if done >= inicio:
            da_semana.append(payload)

    sessoes = len(da_semana)
    minutos = round(sum(_num(p.get("elapsedSec")) for p in da_semana) / 60)
    pcts = [_num(p.get("pct")) for p in da_semana]
    pct_medio = round(sum(pcts) / len(pcts), 1) if pcts else 0.0

    meta_sessoes = int(_num(goals.get("sessionsPerWeek")))
    meta_minutos = int(_num(goals.get("minutesPerWeek")))
    meta_pct = int(_num(goals.get("targetPct")))

    atrasado = (meta_sessoes > 0 and sessoes < meta_sessoes) or (
        meta_minutos > 0 and minutos < meta_minutos
    )
    return Resumo(
        sessoes=sessoes,
        meta_sessoes=meta_sessoes,
        minutos=minutos,
        meta_minutos=meta_minutos,
        pct_medio=pct_medio,
        meta_pct=meta_pct,
        estudou_hoje=estudou_hoje,
        atrasado=atrasado,
    )


def revisoes_vencidas(
    reviews: list[dict[str, Any]], *, now: datetime
) -> int:
    """Itens do caderno de erros com ``dueAt`` (epoch ms) ja passado."""
    agora_ms = _local(now).timestamp() * 1000
    total = 0
    for row in reviews:
        payload = parse_payload(row)
        due = _num(payload.get("dueAt"))
        if due and due <= agora_ms:
            total += 1
    return total


def formatar_resumo(resumo: Resumo) -> str:
    """Texto curto em pt-BR no estilo das demais falas da Gaia."""
    partes: list[str] = []
    if resumo.meta_sessoes:
        partes.append(f"{resumo.sessoes}/{resumo.meta_sessoes} simulados")
    else:
        partes.append(f"{resumo.sessoes} simulado(s)")
    if resumo.meta_minutos:
        partes.append(f"{resumo.minutos}/{resumo.meta_minutos} min")
    elif resumo.minutos:
        partes.append(f"{resumo.minutos} min")
    if resumo.sessoes:
        if resumo.meta_pct:
            partes.append(f"nota média {resumo.pct_medio:.1f}% (meta {resumo.meta_pct}%)")
        else:
            partes.append(f"nota média {resumo.pct_medio:.1f}%")
    return "; ".join(partes)
