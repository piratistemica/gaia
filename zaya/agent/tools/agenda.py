"""Ferramentas da Agenda: leitura e escrita direto no Supabase, sem shell.

Motivo de existirem: no canal do Telegram o agente nao deve depender de
``bash`` para falar com a Agenda. Com estas ferramentas o acesso a agenda fica
possivel sem abrir shell na maquina (ver ``telegram.shell_access``).
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from zaya.agenda.client import AgendaClient, AgendaError, _today_local
from zaya.agent.errors import ToolError
from zaya.agent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    boolean,
    schema,
    string,
)

PAGE_LIMIT = 4000
PAGES_LISTED = 20


def _client(ctx: ToolContext) -> AgendaClient:
    if ctx.agenda is None:
        raise ToolError("Agenda nao configurada")
    client = AgendaClient(ctx.agenda)
    if not client.enabled:
        raise ToolError(
            "Agenda desabilitada: falta VITE_SUPABASE_URL, "
            "SUPABASE_SERVICE_ROLE_KEY ou AGENDA_USER_ID no agenda.env_file"
        )
    return client


def _run(coro):
    """Os handlers sao sincronos (rodam numa thread), o cliente e assincrono."""
    try:
        return asyncio.run(coro)
    except AgendaError as exc:
        raise ToolError(str(exc)) from exc


def _day_label(raw: str) -> str:
    texto = str(raw or "").strip()
    if not texto:
        return _today_local()
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError as exc:
        raise ToolError(f"data invalida: {texto} (use AAAA-MM-DD)") from exc


def _short(texto: object, limit: int = 80) -> str:
    value = " ".join(str(texto or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ---------------------------------------------------------------- leitura
def agenda_dia(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    day = _day_label(str(arguments.get("data") or ""))
    notes = _run(_client(ctx).notes_on(day))
    if not notes:
        return ToolResult.success(f"Agenda de {day}: nada registrado.")

    tarefas = [
        note
        for note in notes
        if note.get("is_checkbox") and not note.get("routine_id")
    ]
    pendentes = [
        note for note in tarefas if not note.get("completed") and _short(note.get("text"))
    ]
    feitas = [note for note in tarefas if note.get("completed") and _short(note.get("text"))]
    rotinas = [note for note in notes if note.get("routine_id")]

    lines = [f"Agenda de {day}"]
    lines.append(f"Pendentes ({len(pendentes)}):")
    if pendentes:
        for note in pendentes:
            lines.append(f"- ☐ {_short(note.get('text'))} [id: {note.get('id')}]")
    else:
        lines.append("- (nenhuma)")
    lines.append(f"Feitas ({len(feitas)}):")
    lines.extend([f"- ☑ {_short(note.get('text'))}" for note in feitas] or ["- (nenhuma)"])
    if rotinas:
        prontas = sum(1 for note in rotinas if note.get("completed"))
        lines.append(f"Rotina do dia: {prontas}/{len(rotinas)} feitas")
        # Item de rotina tambem se marca como feito, entao vai com id.
        devendo = [
            note
            for note in rotinas
            if not note.get("completed") and _short(note.get("text"))
        ]
        for note in devendo:
            lines.append(
                f"- ☐ {_short(note.get('text'))} (rotina) [id: {note.get('id')}]"
            )
    return ToolResult.success("\n".join(lines))


def agenda_rotinas(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    rows = _run(_client(ctx).routines())
    if not rows:
        return ToolResult.success("Nenhuma rotina cadastrada na Agenda.")
    lines = ["Rotinas da Agenda:"]
    for row in rows:
        config = row.get("frequency_config")
        detalhe = ""
        if isinstance(config, dict):
            dias = config.get("days")
            if isinstance(dias, list) and dias:
                # A Agenda indexa como o JS: 0 = domingo, 1 = segunda.
                nomes = ("dom", "seg", "ter", "qua", "qui", "sex", "sáb")
                detalhe = " " + ", ".join(
                    nomes[int(d)] for d in dias if isinstance(d, int) and 0 <= int(d) < 7
                )
        lines.append(f"- {_short(row.get('text'))} ({row.get('type') or 'daily'}{detalhe})")
    return ToolResult.success("\n".join(lines))


def agenda_paginas(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    titulo = " ".join(str(arguments.get("titulo") or "").split())
    client = _client(ctx)
    if not titulo:
        rows = _run(client.pages_meta(PAGES_LISTED))
        if not rows:
            return ToolResult.success("Nenhuma pagina na Agenda.")
        linhas = [f"Paginas da Agenda ({len(rows)} mais recentes):"]
        linhas.extend(f"- {_short(row.get('title'), 60)}" for row in rows)
        return ToolResult.success("\n".join(linhas))

    rows = _run(client.pages(PAGES_LISTED))
    alvo = next(
        (row for row in rows if titulo.casefold() in str(row.get("title") or "").casefold()),
        None,
    )
    if alvo is None:
        return ToolResult.failure(f"nao achei uma pagina com '{titulo}' na Agenda")
    conteudo = str(alvo.get("content") or "")
    if len(conteudo) > PAGE_LIMIT:
        conteudo = conteudo[:PAGE_LIMIT] + "\n\n[conteudo truncado]"
    return ToolResult.success(f"# {alvo.get('title')}\n\n{conteudo}")


# ---------------------------------------------------------------- escrita
def _confirmado(arguments: dict[str, Any]) -> bool:
    return bool(arguments.get("confirm"))


def agenda_nova_tarefa(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    texto = " ".join(str(arguments.get("texto") or "").split())
    if not texto:
        return ToolResult.failure("texto da tarefa vazio")
    day = _day_label(str(arguments.get("data") or ""))
    if not _confirmado(arguments):
        return ToolResult.failure(
            "Confirmacao necessaria: mostre a Talita que a tarefa "
            f"'{texto}' sera criada na Agenda de {day} e repita a chamada com "
            "confirm=true."
        )
    row = _run(
        _client(ctx).create_task(
            texto, date=day, parent_id=str(arguments.get("parent_id") or "")
        )
    )
    return ToolResult.success(
        f"Tarefa criada na Agenda de {day}: {texto} [id: {row.get('id')}]"
    )


def agenda_marcar_tarefa(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    day = _day_label(str(arguments.get("data") or ""))
    completar = arguments.get("feito")
    done = True if completar is None else bool(completar)
    ident = str(arguments.get("id") or "").strip()
    client = _client(ctx)

    alvo = ""
    rotulo = ""
    if ident:
        alvo, rotulo = ident, ident
    else:
        procurado = " ".join(str(arguments.get("texto") or "").split()).casefold()
        if not procurado:
            return ToolResult.failure("informe o id ou o texto da tarefa")
        notes = _run(client.notes_on(day))
        candidatos = [
            note
            for note in notes
            if note.get("is_checkbox")
            and _short(note.get("text")).casefold() == procurado
        ] or [
            note
            for note in notes
            if note.get("is_checkbox")
            and procurado in _short(note.get("text"), 400).casefold()
        ]
        if not candidatos:
            return ToolResult.failure(f"nao achei tarefa com '{procurado}' em {day}")
        if len(candidatos) > 1:
            opcoes = "; ".join(
                f"{_short(note.get('text'), 40)} [id: {note.get('id')}]"
                for note in candidatos
            )
            return ToolResult.failure(f"mais de uma tarefa combina: {opcoes}")
        alvo = str(candidatos[0].get("id"))
        rotulo = _short(candidatos[0].get("text"))

    if not _confirmado(arguments):
        acao = "marcar como feita" if done else "reabrir"
        return ToolResult.failure(
            f"Confirmacao necessaria: '{rotulo}' sera {acao} na Agenda de {day}. "
            "Mostre isso a Talita e repita a chamada com confirm=true."
        )
    _run(client.set_completed(alvo, done))
    estado = "feita" if done else "reaberta"
    return ToolResult.success(f"Tarefa {estado}: {rotulo}")


AGENDA_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "agenda_dia",
        "description": (
            "Le a Agenda de um dia (padrao hoje): tarefas pendentes com id, "
            "tarefas feitas e o placar da rotina. Use antes de responder sobre "
            "o dia e antes de marcar qualquer coisa."
        ),
        "parameters": schema(
            {"data": string("Dia no formato AAAA-MM-DD; vazio = hoje")}
        ),
        "handler": agenda_dia,
    },
    {
        "name": "agenda_rotinas",
        "description": "Lista as rotinas cadastradas na Agenda e os dias de cada uma.",
        "parameters": schema({}),
        "handler": agenda_rotinas,
    },
    {
        "name": "agenda_paginas",
        "description": (
            "Sem 'titulo', lista as paginas da Agenda; com 'titulo', devolve o "
            "conteudo da primeira pagina que casar."
        ),
        "parameters": schema(
            {"titulo": string("Trecho do titulo da pagina; vazio = listar")}
        ),
        "handler": agenda_paginas,
    },
    {
        "name": "agenda_nova_tarefa",
        "description": (
            "Cria uma tarefa na Agenda. Exige confirm=true: primeiro mostre o "
            "que sera criado, depois repita com confirm=true."
        ),
        "parameters": schema(
            {
                "texto": string("Texto da tarefa"),
                "data": string("Dia AAAA-MM-DD; vazio = hoje"),
                "parent_id": string("Opcional: id da tarefa-mae (subtarefa)"),
                "confirm": boolean("true somente apos a Talita concordar"),
            },
            ["texto"],
        ),
        "handler": agenda_nova_tarefa,
        "requires_approval": True,
    },
    {
        "name": "agenda_marcar_tarefa",
        "description": (
            "Marca uma tarefa como feita (ou reabre, com feito=false). Aceita "
            "'id' ou 'texto'. Exige confirm=true depois de mostrar o que muda."
        ),
        "parameters": schema(
            {
                "id": string("Id da tarefa (preferivel)"),
                "texto": string("Texto da tarefa, se nao souber o id"),
                "data": string("Dia AAAA-MM-DD; vazio = hoje"),
                "feito": boolean("Padrao true; false reabre a tarefa"),
                "confirm": boolean("true somente apos a Talita concordar"),
            }
        ),
        "handler": agenda_marcar_tarefa,
        "requires_approval": True,
    },
)


def build_agenda_tools() -> tuple[Tool, ...]:
    return tuple(Tool(**spec) for spec in AGENDA_TOOLS)


__all__ = ["AGENDA_TOOLS", "build_agenda_tools"]
