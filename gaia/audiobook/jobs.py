"""Geracao do audiobook: texto -> trechos narrados -> MP3 com ID3 e faixas.

Sem dependencia extra: o MP3 de cada trecho vem do edge-tts, os trechos sao
concatenados (MP3 e uma sequencia de frames) e os metadados ID3v2 (titulo,
autor, capitulos) sao escritos a mao. Retoma de onde parou: cada trecho ja
narrado fica em ``tmp/<job>/NNNN.mp3`` e e reaproveitado numa nova tentativa.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from gaia.audiobook.config import AudiobookConfig
from gaia.audiobook.extract import Extracted, extract
from gaia.audiobook.normalize import chapters, paragraphs, prepare, split_for_tts

SUPPORTED = {".pdf", ".txt", ".md", ".markdown", ".text"}

# Bitrates do MP3 padrao para estimar a duracao (o edge-tts usa 48 kbps mono
# para as vozes neurais; ~6 KB/s por segundo de fala).
_EDGE_BYTES_PER_SECOND = 6000


class AudiobookError(Exception):
    pass


@dataclass
class ChapterMark:
    title: str
    start: int
    end: int = 0


@dataclass
class AudiobookJob:
    """Um audiobook: entrada, saída e o rastro da geração."""

    source: Path
    outbox: Path
    cfg: AudiobookConfig
    tmp_dir: Path
    title: str = ""
    text: str = ""
    direct_text: bool = False
    chapters: list[ChapterMark] = field(default_factory=list)
    output: Path | None = None
    paragraphs_done: int = 0
    paragraphs_total: int = 0

    def __post_init__(self) -> None:
        if not self.title:
            self.title = _title_from(self.source, self.direct_text)
        key = hashlib.sha1(str(self.source.resolve()).encode()).hexdigest()[:12]
        self.workdir: Path = self.tmp_dir / key


def _slug(text: str, limit: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "audiobook")[:limit]


def _title_from(path: Path, direct: bool) -> str:
    if direct:
        return "Texto colado"
    stem = path.stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:120] or path.stem


async def _synthesize_chunk(cfg: AudiobookConfig, text: str, target: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(
        text, cfg.voice, rate=cfg.rate, pitch=cfg.pitch
    )
    await communicate.save(str(target))


async def build_audiobook(job: AudiobookJob, progress=None) -> Path:
    """Gera o MP3 final e devolve o caminho.

    ``progress(done, total)`` e chamado depois de cada trecho narrado; serve
    para atualizar a mensagem do Telegram sem reescrever o arquivo a cada vez.
    """
    cfg = job.cfg
    extracted = _extract(job)
    job.text = extracted.text
    parts = prepare(extracted, job.title) or [(job.title, extracted.text.strip())]
    if not parts:
        raise AudiobookError("não sobrou texto para narrar depois da limpeza")

    job.workdir.mkdir(parents=True, exist_ok=True)
    job.outbox.mkdir(parents=True, exist_ok=True)

    planned: list[tuple[str, list[str]]] = [
        (title, split_for_tts(body, cfg.chunk_chars)) for title, body in parts
    ]
    total = sum(len(chunks) for _, chunks in planned if chunks)
    job.paragraphs_total = total
    if total == 0:
        raise AudiobookError("não sobrou texto para narrar")

    merged = job.workdir / "merged.mp3"
    marks: list[ChapterMark] = []
    with merged.open("wb") as out:
        offset = 0
        done = 0
        for title, chunks in planned:
            if not chunks:
                continue
            mark = ChapterMark(title, offset)
            for index, chunk in enumerate(chunks):
                piece = job.workdir / f"{done:05d}.mp3"
                if not (cfg.resume and piece.is_file() and piece.stat().st_size > 0):
                    await _retry(lambda text=chunk, target=piece: _synthesize_chunk(cfg, text, target))
                out.write(piece.read_bytes())
                out.flush()
                if cfg.sentence_pause_ms > 0 and index + 1 < len(chunks):
                    await asyncio.sleep(cfg.sentence_pause_ms / 1000)
                done += 1
                job.paragraphs_done = done
                if progress is not None:
                    progress(done, total)
                offset = out.tell()
            mark.end = offset
            marks.append(mark)

    job.chapters = marks
    output = job.outbox / f"{_slug(job.title)}.mp3"
    output.write_bytes(_tag(merged.read_bytes(), job.title, marks, job.source))
    job.output = output
    job.paragraphs_done = total
    if progress is not None:
        progress(total, total)
    for piece in job.workdir.glob("*.mp3"):
        piece.unlink(missing_ok=True)
    merged.unlink(missing_ok=True)
    return output


async def _retry(action, attempts: int = 3) -> None:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await action()
        except Exception as exc:  # rede/API do edge-tts: tenta de novo
            last = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise AudiobookError(f"falha ao gerar trecho: {last}") from last


def _extract(job: AudiobookJob) -> Extracted:
    if job.direct_text or job.source.suffix.lower() not in SUPPORTED:
        return extract_text_from_string(job.text)
    try:
        extracted = extract(job.source)
    except ValueError as exc:
        raise AudiobookError(str(exc)) from exc
    except OSError as exc:
        raise AudiobookError(f"não consegui ler {job.source.name}: {exc}") from exc
    if not extracted.readable:
        raise AudiobookError(
            f"{job.source.name}: texto não extraído (PDF escaneado ou fonte não "
            "suportada). Passe por um OCR ou envie .txt."
        )
    return extracted


def extract_text_from_string(text: str) -> Extracted:
    from gaia.audiobook.extract import extract_text

    return extract_text(Path("texto.txt"), text)


# ------------------------------------------------------------------ ID3
def _frame(frame_id: bytes, text: str) -> bytes:
    payload = b"\x03" + text.encode("utf-8")  # encoding 3 = UTF-8
    size = len(payload)
    header = frame_id + bytes(
        [(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F]
    ) + b"\x00\x00"
    return header + payload


def _tag(data: bytes, title: str, marks: list[ChapterMark], source: Path) -> bytes:
    """Escreve ID3v2.3 (titulo, autor, capitulos) a frente dos frames MP3."""
    chapters = []
    for order, mark in enumerate(marks):
        chapters.append((mark.title, mark.start, mark.end, order))
    frames = _frame(b"TIT2", title) + _frame(b"TPE1", "Gaia")
    frames += _frame(b"TALB", f"Audiolivro · {source.name}")
    for title_, start, end, order in chapters:
        frames += _chapters_frame(start, end, order, title_)
    size = len(frames)
    syncsafe = bytes(
        [(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F]
    )
    header = b"ID3\x03\x00\x00" + syncsafe
    return header + frames + data


def _chapters_frame(start: int, end: int, order: int, title: str) -> bytes:
    """CHAP: elemento com tempo de inicio/fim em ms (estimado pelo tamanho)."""
    millis_start = int(start / _EDGE_BYTES_PER_SECOND * 1000)
    millis_end = int(end / _EDGE_BYTES_PER_SECOND * 1000)
    start_body = b"\x00" + millis_start.to_bytes(4, "big") + b"\xff\xff\xff"
    end_body = b"\x00" + millis_end.to_bytes(4, "big") + b"\xff\xff\xff"
    title_body = b"\x03" + title.encode("utf-8")
    payload = (
        start_body
        + end_body
        + start_body[1:5]  # startTime = startOffset
        + end_body[1:5]    # endTime = endOffset
        + _element(b"TIT2", title_body)
    )
    size = len(payload)
    frame_header = b"CHAP" + bytes(
        [(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F]
    ) + b"\x00\x00"
    return frame_header + payload


def _element(element_id: bytes, body: bytes) -> bytes:
    size = len(body)
    return element_id + size.to_bytes(4, "big") + b"\x00\x00" + body


__all__ = [
    "SUPPORTED",
    "AudiobookError",
    "AudiobookJob",
    "ChapterMark",
    "build_audiobook",
    "chapters",
    "extract_text_from_string",
    "paragraphs",
]
