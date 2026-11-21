"""Ferramentas padrao do agente."""

from __future__ import annotations

from pathlib import Path

from gaia.agenda.client import AgendaClient
from gaia.agent.tools.agenda import build_agenda_tools
from gaia.agent.tools.base import Tool, ToolContext, ToolResult, ToolSet
from gaia.agent.tools.filesystem import (
    EDIT_TOOL,
    GLOB_TOOL,
    GREP_TOOL,
    LIST_TOOL,
    READ_TOOL,
    WRITE_TOOL,
)
from gaia.agent.tools.planilhas import build_planilha_tools
from gaia.agent.tools.shell import BASH_TOOL


def build_tools(ctx: ToolContext) -> ToolSet:
    tools = ToolSet()
    tools.add(
        Tool(**READ_TOOL),
        Tool(**WRITE_TOOL),
        Tool(**EDIT_TOOL),
        Tool(**LIST_TOOL),
        Tool(**GLOB_TOOL),
        Tool(**GREP_TOOL),
        Tool(**BASH_TOOL),
    )
    # So aparecem quando ha credencial da Agenda: sem isso o modelo tentaria
    # usar uma ferramenta que nunca vai funcionar.
    if ctx.agenda is not None and AgendaClient(ctx.agenda).enabled:
        tools.add(*build_agenda_tools())
    # Planilhas: so quando existe um SecondBrain (a ferramenta le de la).
    if str(ctx.secondbrain or "").strip() and Path(ctx.secondbrain).expanduser().is_dir():
        tools.add(*build_planilha_tools())
    return tools


__all__ = ["Tool", "ToolContext", "ToolResult", "ToolSet", "build_tools"]
