"""Ferramentas de progresso de estudos: leitura do Abin Suite (Supabase).

Mesmo motivo da Agenda: no Telegram o agente nao deve depender de ``bash`` para
falar com o Supabase. Apenas leitura — nunca escreve no banco do app.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from gaia.agent.errors import ToolError
from gaia.agent.tools.base import Tool, ToolContext, ToolResult, schema
from gaia.estudos.client import EstudosClient, EstudosError
from gaia.estudos.progresso import (
    formatar_resumo,
    resumo_semana,
    revisoes_vencidas,
)


def _client(ctx: ToolContext) -> EstudosClient:
    if ctx.estudos is None:
        raise ToolError("Estudos nao configurados")
    client = EstudosClient(ctx.estudos)
    if not client.enabled:
        raise ToolError(
            "Estudos desabilitados: falta VITE_SUPABASE_URL, "
            "SUPABASE_SERVICE_ROLE_KEY ou ESTUDOS_USER_ID no estudos.env_file"
        )
    return client


def _run(coro):
    """Os handlers sao sincronos (rodam numa thread), o cliente e assincrono."""
    try:
        return asyncio.run(coro)
    except EstudosError as exc:
        raise ToolError(str(exc)) from exc


def _now() -> datetime:
    return datetime.now().astimezone()


def estudos_progresso(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    client = _client(ctx)
    results = _run(client.results())
    goals = _run(client.goals())
    resumo = resumo_semana(results, goals, now=_now())
    if not resumo.sessoes and not resumo.estudou_hoje:
        return ToolResult.success(
            "Estudos (Abin Suite): nenhuma tentativa encontrada nesta semana. "
            + _meta_linha(resumo)
        )
    estado = "estudou hoje" if resumo.estudou_hoje else "ainda nao estudou hoje"
    if not resumo.meta_sessoes and not resumo.meta_minutos:
        marca = "sem meta definida"
    else:
        marca = "meta em dia" if not resumo.atrasado else "meta atrasada"
    return ToolResult.success(
        f"Estudos (Abin Suite) nesta semana: {formatar_resumo(resumo)}. "
        f"{estado}; {marca}."
    )


def _meta_linha(resumo) -> str:
    partes: list[str] = []
    if resumo.meta_sessoes:
        partes.append(f"{resumo.meta_sessoes} simulados/semana")
    if resumo.meta_minutos:
        partes.append(f"{resumo.meta_minutos} min/semana")
    if resumo.meta_pct:
        partes.append(f"nota alvo {resumo.meta_pct}%")
    return ("Meta: " + ", ".join(partes) + ".") if partes else "Sem meta semanal configurada."


def estudos_metas(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    goals = _run(_client(ctx).goals())
    partes: list[str] = []
    if goals.get("sessionsPerWeek"):
        partes.append(f"{int(goals['sessionsPerWeek'])} simulados por semana")
    if goals.get("minutesPerWeek"):
        partes.append(f"{int(goals['minutesPerWeek'])} minutos por semana")
    if goals.get("targetPct"):
        partes.append(f"nota alvo {int(goals['targetPct'])}%")
    if not partes:
        return ToolResult.success("Nenhuma meta semanal configurada no Abin Suite.")
    return ToolResult.success("Metas de estudo: " + ", ".join(partes) + ".")


def estudos_pendentes(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    reviews = _run(_client(ctx).reviews())
    total = revisoes_vencidas(reviews, now=_now())
    if not reviews:
        return ToolResult.success("Caderno de erros vazio no Abin Suite.")
    if total == 0:
        return ToolResult.success(
            f"Caderno de erros: {len(reviews)} item(ns), nenhum para revisar hoje."
        )
    return ToolResult.success(
        f"Caderno de erros: {total} item(ns) para revisar hoje "
        f"({len(reviews)} no total)."
    )


ESTUDOS_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "estudos_progresso",
        "description": (
            "Mostra o progresso dos simulados do Abin Suite nesta semana: "
            "sessoes, minutos, nota liquida media, se a meta esta em dia e se "
            "estudou hoje. Use quando a Talita perguntar como esta o estudo."
        ),
        "parameters": schema({}),
        "handler": estudos_progresso,
    },
    {
        "name": "estudos_metas",
        "description": "Mostra as metas semanais de estudo (simulados, minutos e nota alvo).",
        "parameters": schema({}),
        "handler": estudos_metas,
    },
    {
        "name": "estudos_pendentes",
        "description": (
            "Diz quantos itens do caderno de erros estao para revisar hoje "
            "(revisao espacada)."
        ),
        "parameters": schema({}),
        "handler": estudos_pendentes,
    },
)


def build_estudos_tools() -> tuple[Tool, ...]:
    return tuple(Tool(**spec) for spec in ESTUDOS_TOOLS)


__all__ = ["ESTUDOS_TOOLS", "build_estudos_tools"]
