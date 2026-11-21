"""Configuracao do audiobook ([audiobook] do config.toml)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class AudiobookConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Vazio => <data_dir>/audiobooks/inbox. Os PDFs/TXT ficam aqui.
    inbox: str = ""
    # Vazio => <data_dir>/audiobooks/outbox. O MP3 pronto sai aqui.
    outbox: str = ""
    # Vazio => <data_dir>/audiobooks/tmp (MP3 por trecho, apagados no fim).
    tmp: str = ""
    # Voz neural do edge-tts. Vozes pt-BR: pt-BR-ThalitaNeural,
    # pt-BR-FranciscaNeural, pt-BR-AntonioNeural, pt-BR-BrendaNeural.
    voice: str = "pt-BR-ThalitaNeural"
    rate: str = "-4%"
    pitch: str = "+0Hz"
    # Pausa entre trechos (ms). Da respiro e evita frases coladas.
    sentence_pause_ms: int = 220
    # Trecho enviado ao edge-tts de uma vez. Maior = menos pausas e menos
    # requisicoes; muito grande pode estourar a resposta do servico.
    chunk_chars: int = 3500
    # Checar a pasta de entrada de tempos em tempos (0 = so por comando).
    watch_minutes: float = 0.0
    # Avisar no Telegram quando um audiobook fica pronto.
    notify: bool = True
    # Retomar de onde parou depois de queda/reinicio (cache por trecho).
    resume: bool = True

    def inbox_path(self, data_dir: Path) -> Path:
        return self._path(self.inbox, data_dir / "audiobooks" / "inbox")

    def outbox_path(self, data_dir: Path) -> Path:
        return self._path(self.outbox, data_dir / "audiobooks" / "outbox")

    def tmp_path(self, data_dir: Path | None = None) -> Path:
        """``tmp`` configurado vence; senao ``<data_dir>/audiobooks/tmp``."""
        if self.tmp:
            return Path(self.tmp).expanduser().resolve()
        return (data_dir / "audiobooks" / "tmp").resolve() if data_dir else Path.cwd()

    @staticmethod
    def _path(configured: str, default: Path) -> Path:
        return Path(configured).expanduser().resolve() if configured else default.resolve()
