"""Ferramenta ``audiobook``: narra um PDF/texto e deixa o MP3 pronto.

A geracao e longa (minutos por livro), entao a ferramenta roda ``build_audiobook``
como tarefa de fundo e devolve na hora o caminho da pasta de saida. O canal
(Telegram) acompanha o progresso e entrega o arquivo quando a tarefa termina.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia.agent.errors import ToolError
from gaia.agent.tools.base import Tool, ToolContext, ToolResult, schema, string
from gaia.audiobook.jobs import AudiobookError, AudiobookJob, build_audiobook
from gaia.audiobook.service import SUPPORTED
from gaia.audiobook.service import text_job as make_text_job

if TYPE_CHECKING:
    from gaia.audiobook.config import AudiobookConfig


def _cfg(ctx: ToolContext) -> AudiobookConfig:
    cfg = getattr(ctx, "audiobook", None)
    if cfg is None:
        raise ToolError("audiobook nao configurado")
    return cfg


def _locate(raw: str, cfg: AudiobookConfig, ctx: ToolContext) -> Path:
    """Resolve o pedido do modelo: caminho direto, pasta padrao ou nome solto."""
    if not raw.strip():
        raise ToolError("informe o arquivo (PDF/TXT) ou o texto a narrar")
    inbox = cfg.inbox_path(ctx.directory)
    candidates = [Path(raw).expanduser()]
    candidates.append(inbox / raw)
    for candidate in candidates:
        if candidate.is_file():
            if candidate.suffix.lower() not in SUPPORTED:
                raise ToolError(f"{candidate.name}: use .pdf ou .txt")
            return candidate.resolve()
    try:
        direct = ctx.resolve(raw)
    except ToolError:
        direct = None
    if direct is not None and direct.is_file():
        return direct
    available = sorted(p.name for p in inbox.glob("*") if p.suffix.lower() in SUPPORTED)
    hint = ", ".join(available[:10]) or "nenhum arquivo"
    raise ToolError(f"nao achei '{raw}'. Na pasta de entrada: {hint}")


def audiobook(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    cfg = _cfg(ctx)
    if not cfg.enabled:
        return ToolResult.failure("audiobook desligado (audiobook.enabled=false)")
    arquivo = str(arguments.get("arquivo") or "").strip()
    texto = str(arguments.get("texto") or "").strip()
    titulo = str(arguments.get("titulo") or "").strip()
    outbox = cfg.outbox_path(ctx.directory)
    tmp_dir = cfg.tmp_path(ctx.directory)
    if texto and not arquivo:
        if len(texto) < 200:
            return ToolResult.failure("texto curto demais para um audiobook (min. 200 caracteres)")
        job = make_text_job(cfg, outbox, tmp_dir, texto, titulo)
        origem = "texto colado"
    elif arquivo:
        source = _locate(arquivo, cfg, ctx)
        job = AudiobookJob(source=source, outbox=outbox, cfg=cfg, tmp_dir=tmp_dir, title=titulo)
        origem = source.name
    else:
        return ToolResult.failure("informe 'arquivo' (PDF/TXT) ou 'texto'")

    task = asyncio.create_task(build_audiobook(job))
    waiter = getattr(ctx, "audiobook_watch", None)
    if waiter is not None:
        waiter(task, job)
    else:
        task.add_done_callback(_log_done)
    return ToolResult.success(
        f"Narração iniciada: {origem} → {job.title}. "
        f"Saída: {job.outbox}. Aviso aqui quando o MP3 estiver pronto."
    )


def _log_done(task: asyncio.Task) -> None:
    logger = logging.getLogger("gaia.audiobook")
    try:
        output = task.result()
    except (AudiobookError, asyncio.CancelledError) as exc:
        logger.warning("audiobook falhou: %s", exc)
    except Exception:
        logger.exception("audiobook falhou")
    else:
        logger.info("audiobook pronto: %s", output)


AUDIOBOOK_TOOL: dict[str, Any] = {
    "name": "audiobook",
    "description": (
        "Gera um audiolivro (MP3 narrado com voz neural) a partir de um PDF ou "
        "texto. Passe 'arquivo' com o nome de um PDF/TXT da pasta de audiobooks, "
        "ou 'texto' com o conteudo a narrar. Roda em segundo plano: a resposta "
        "chega quando terminar. Nao use para respostas curtas; para ler um trecho "
        "pequeno em voz alta use a fala normal."
    ),
    "parameters": schema(
        {
            "arquivo": string("Nome ou caminho de um PDF/TXT para narrar"),
            "texto": string("Texto colado a narrar (quando nao ha arquivo)"),
            "titulo": string("Titulo do audiolivro (opcional)"),
        }
    ),
    "handler": audiobook,
    "requires_approval": False,
}


def build_audiobook_tools() -> tuple[Tool, ...]:
    return (Tool(**AUDIOBOOK_TOOL),)


__all__ = ["AUDIOBOOK_TOOL", "audiobook", "build_audiobook_tools"]
