"""Lote da pasta de entrada: cada PDF/texto vira um MP3 na pasta de saida.

O estado e o proprio disco: um arquivo em ``inbox`` ainda nao foi feito; ao
terminar ele vai para ``processados`` (sucesso) ou ``erros`` (falhou). Assim, um
restart nunca regenera o que ja existe e nao ha banco para sincronizar.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from gaia.audiobook.config import AudiobookConfig
from gaia.audiobook.jobs import SUPPORTED, AudiobookError, AudiobookJob, build_audiobook

# progress(nome_do_arquivo, trechos_prontos, trechos_no_total)
Progress = Callable[[str, int, int], None]


@dataclass
class BatchResult:
    done: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.done and not self.failed

    def summary(self) -> str:
        lines = [f"✅ {path.name}" for path in self.done]
        lines += [f"❌ {source.name}: {error}" for source, error in self.failed]
        return "\n".join(lines)


def pending(cfg: AudiobookConfig, inbox: Path) -> list[Path]:
    if not inbox.is_dir():
        return []
    files = [
        path
        for path in inbox.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED and not path.name.startswith(".")
    ]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def _busy(path: Path) -> bool:
    """Arquivo ainda sendo copiado: tamanho mudou no intervalo de 1s."""
    try:
        first = path.stat().st_size
        time.sleep(1.0)
        return path.stat().st_size != first
    except OSError:
        return True


async def _looks_busy(path: Path) -> bool:
    return await asyncio.to_thread(_busy, path)


def _archive(source: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / source.name
    if dest.exists():
        dest = target_dir / f"{source.stem}-{int(time.time())}{source.suffix}"
    shutil.move(str(source), str(dest))


async def process_inbox(
    cfg: AudiobookConfig,
    inbox: Path,
    outbox: Path,
    tmp_dir: Path,
    logger: logging.Logger,
    progress: Progress | None = None,
) -> BatchResult:
    result = BatchResult()
    if not cfg.enabled or not inbox.is_dir():
        return result
    for source in pending(cfg, inbox):
        if await _looks_busy(source):
            logger.info("adiando %s: arquivo ainda em cópia", source.name)
            continue
        job = AudiobookJob(source=source, outbox=outbox, cfg=cfg, tmp_dir=tmp_dir)

        def _report(done: int, total: int, name: str = source.name) -> None:
            if progress is not None:
                progress(name, done, total)

        try:
            output = await build_audiobook(job, progress=_report)
        except AudiobookError as exc:
            logger.warning("audiobook falhou em %s: %s", source.name, exc)
            result.failed.append((source, str(exc)))
            _archive(source, inbox / "erros")
        except Exception as exc:
            logger.exception("audiobook falhou em %s", source.name)
            result.failed.append((source, f"erro inesperado: {exc}"))
            _archive(source, inbox / "erros")
        else:
            logger.info("audiobook pronto: %s", output)
            result.done.append(output)
            _archive(source, inbox / "processados")
        await asyncio.sleep(0)
    return result


def text_job(
    cfg: AudiobookConfig,
    outbox: Path,
    tmp_dir: Path,
    text: str,
    title: str = "",
) -> AudiobookJob:
    """Job de texto colado: sem arquivo de entrada, o proprio texto e a fonte."""
    source = outbox / f"{title or 'texto-colado'}.txt"
    return AudiobookJob(
        source=source,
        outbox=outbox,
        cfg=cfg,
        tmp_dir=tmp_dir,
        title=title,
        text=text,
        direct_text=True,
    )
