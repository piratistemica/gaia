"""Voz do chat web: microfone no motor, transcricao local e fala no navegador.

O `gaia serve` ja e dono do microfone e do STT (`Recorder` +
`WhisperTranscriber`); o que faltava era uma porta HTTP para a tela. Aqui o
navegador pede "ouca por N segundos" e recebe o texto transcrito; a resposta da
Gaia e sintetizada pelo mesmo TTS da TUI (`edge`/`say`) e vai como bytes para o
`<audio>` da pagina. Numa maquina headless nao ha caixa de som para tocar: quem
reproduz e o navegador de quem esta conversando.

Contratos que este modulo garante:

* **uma escuta por vez.** Duas abas nao abrem dois streams no mesmo microfone:
  a segunda recebe ``VoiceUnavailable`` (o transporte traduz em 503).
* **silencio nao e erro.** Ninguem falou na janela: ``heard=False`` e texto
  vazio. Quem decide silenciar o microfone e a tela (15 s sem resposta).
* **audio de sobra e apagado.** O mp3/aiff do TTS vive em ``tmp/`` e e removido
  depois de lido; nada fica no disco.

O transporte nao fala com este modulo direto: `gaia/serve/app.py` liga os
metodos opcionais ``listen``/``stop_listening``/``speak`` do `Target`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from gaia.audio.recorder import AudioError
from gaia.stt.faster_whisper import STTError

# Janela padrao de escuta: sem fala nesse tempo, o microfone para sozinho.
LISTEN_WINDOW_SECONDS = 15.0
MIN_WINDOW_SECONDS = 5.0
MAX_WINDOW_SECONDS = 120.0
# Folga sobre a janela. Curta de proposito: o Enter da tela fecha a janela com
# `POST /voice/stop` (o VAD devolve o que ja ouviu), entao esperar mais que
# isso so aumentaria a janela em que o microfone fica aberto sem ninguem.
LISTEN_TIMEOUT_SLACK = 15.0

# O TTS entrega mp3 (edge) ou aiff (say); o `<audio>` decide pelo tipo.
CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}


class VoiceUnavailable(RuntimeError):
    """Sem microfone (ou sem STT) nesta maquina; a tela avisa e sai do modo."""


def clamp_window(value: Any) -> float:
    """Janela de escuta em segundos, dentro do razoavel (5 a 120)."""
    try:
        segundos = float(value)
    except (TypeError, ValueError):
        return LISTEN_WINDOW_SECONDS
    if segundos <= 0:
        return LISTEN_WINDOW_SECONDS
    return max(MIN_WINDOW_SECONDS, min(segundos, MAX_WINDOW_SECONDS))


def content_type(caminho: Path) -> str:
    return CONTENT_TYPES.get(caminho.suffix.lower(), "application/octet-stream")


class WebVoice:
    """Microfone + STT + TTS do motor, expostos para o chat web."""

    def __init__(
        self,
        recorder: Any,
        transcriber: Any,
        tts: Any,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.recorder = recorder
        self.transcriber = transcriber
        self.tts = tts
        self.logger = logger or logging.getLogger(__name__)
        # Uma escuta por vez (duas abas, dois microphones no mesmo stream).
        self._listen_lock = asyncio.Lock()

    # ------------------------------------------------------------- microfone
    def available(self) -> tuple[bool, str]:
        """Microfone e transcricao prontos? (devolve a razao quando nao)."""
        from gaia.audio.recorder import Recorder

        if self.recorder is None:
            return False, "este motor nao tem microfone"
        if self.transcriber is None:
            return False, "transcricao indisponivel neste motor"
        try:
            return Recorder.available()
        except Exception as exc:  # pragma: no cover - depende do sistema
            return False, str(exc)

    def stop(self) -> None:
        """Interrompe a escuta em andamento (botao desligado no meio da fala)."""
        if self.recorder is not None:
            self.recorder.stop()

    async def listen(self, window: float = LISTEN_WINDOW_SECONDS) -> dict[str, Any]:
        """Grava uma fala (janela + VAD) e devolve o texto transcrito."""
        ok, motivo = self.available()
        if not ok:
            raise VoiceUnavailable(motivo)
        if self._listen_lock.locked():
            raise VoiceUnavailable("ja existe uma escuta em andamento")
        async with self._listen_lock:
            audio = await asyncio.to_thread(self._record, clamp_window(window))
            if audio is None or len(audio) == 0:
                return {"text": "", "heard": False}
            texto = await asyncio.to_thread(self._transcribe, audio)
            return {"text": texto, "heard": bool(texto)}

    def _record(self, window: float) -> Any:
        try:
            return self.recorder.record_until_silence(wait_timeout=window)
        except AudioError as exc:
            raise VoiceUnavailable(str(exc)) from exc

    def _transcribe(self, audio: Any) -> str:
        """Transcreve no executor de threads (o modelo e CPU-bound).

        O lock e o `asyncio.Lock` compartilhado com o `Orchestrator`: a TUI, o
        Telegram e o web usam o mesmo `WhisperTranscriber`, e uma inferencia
        por vez e mais rapido que duas disputando os nucleos.
        """
        try:
            return self.transcriber.transcribe(audio)
        except STTError as exc:
            raise VoiceUnavailable(str(exc)) from exc

    # ------------------------------------------------------------------ fala
    async def speech(self, text: str) -> tuple[bytes, str] | None:
        """Sintetiza a resposta e devolve ``(bytes, content-type)``.

        ``None`` = motor sem TTS (ou ``tts.enabled=false``): a tela segue sem
        audio e volta a ouvir, sem tratar como erro.
        """
        if self.tts is None or not (text or "").strip():
            return None
        try:
            preparado = await self.tts.prepare(text)
        except Exception as exc:  # pragma: no cover - rede/edge
            self.logger.warning("TTS do web falhou: %s", exc)
            return None
        if preparado is None:
            return None
        backend, caminho = preparado
        try:
            dados = await asyncio.to_thread(caminho.read_bytes)
        finally:
            backend.cleanup(caminho)
        return dados, content_type(caminho)


__all__ = [
    "LISTEN_TIMEOUT_SLACK",
    "LISTEN_WINDOW_SECONDS",
    "MAX_WINDOW_SECONDS",
    "MIN_WINDOW_SECONDS",
    "VoiceUnavailable",
    "WebVoice",
    "clamp_window",
    "content_type",
]
