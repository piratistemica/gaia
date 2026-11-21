"""Audiobooks: PDF/texto -> narracao neural em MP3.

Pasta vigiada (`audiobook.inbox`) -> MP3 pronto em `audiobook.outbox`.
Os PDFs vao para ``processados`` depois do sucesso e ``erros`` quando a
extracao falha, para nao reprocessarem em loop a cada checagem.
"""

from gaia.audiobook.config import AudiobookConfig
from gaia.audiobook.extract import extract_pdf, extract_text
from gaia.audiobook.jobs import AudiobookError, AudiobookJob, build_audiobook

__all__ = [
    "AudiobookConfig",
    "AudiobookError",
    "AudiobookJob",
    "build_audiobook",
    "extract_pdf",
    "extract_text",
]
