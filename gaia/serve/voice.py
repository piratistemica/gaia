"""Voz do chat web: microfone no motor, transcricao local e fala no navegador.

O `gaia serve` ja e dono do microfone e do STT (`Recorder` +
`WhisperTranscriber`); o que faltava era uma porta HTTP para a tela. Aqui o
navegador pede "ouca por N segundos" e recebe o texto transcrito; a resposta da
Gaia e sintetizada pelo mesmo TTS da TUI (`edge`/`say`) e vai como bytes para o
`<audio>` da pagina. Numa maquina headless nao ha caixa de som para tocar: quem
reproduz e o navegador de quem esta conversando.

Contratos que este modulo garante:

* **duas portas, um Whisper.** O modo antigo grava no microfone do motor
  (``POST /voice/listen``); o novo recebe o WAV do navegador de quem conversa
  (``POST /voice/upload``), porque o mini headless nao tem microfone util mas
  tem o faster-whisper local. O upload nao toca o disco: decodifica em memoria
  e descarta. So WAV/PCM e aceito para nao arrastar ffmpeg/librosa para o
  servidor.
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
import struct
from pathlib import Path
from typing import Any

import numpy as np

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

# Fases publicadas em `GET /voice` (a tela desenha o botao a partir delas).
STATE_IDLE = "idle"
STATE_LISTENING = "ouvindo"
STATE_TRANSCRIBING = "transcrevendo"

# Taxa que o faster-whisper espera. O navegador reamostra antes de enviar, mas
# o decodificador tambem reamostra (Firefox/Safari gravam em 44.1/48 kHz).
SAMPLE_RATE = 16000
# Teto do audio aceito no upload: 3 minutos de fala em 16-bit 16 kHz (~5,5 MB).
# O transporte tambem limita o Content-Length (a rota responde 413).
MAX_UPLOAD_SECONDS = 180.0

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


def decode_wav(data: bytes) -> np.ndarray:
    """WAV (PCM 8/16 bits ou float 32) -> ``float32`` mono @ 16 kHz em [-1, 1].

    Percorre os chunks de verdade: ``fmt ``/``data`` nao estao sempre no mesmo
    offset (o Chrome grava um ``LIST`` antes do ``data``). Qualquer anomalia
    vira ``ValueError``, que o transporte traduz em 400 ``invalid_audio`` —
    audio de terceiros nunca chega ao Whisper pela metade.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("o audio enviado nao e WAV")

    formato = canais = taxa = bits = 0
    amostras: bytes | None = None
    pos = 12
    while pos + 8 <= len(data):
        chunk = data[pos : pos + 4]
        tamanho = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        corpo = data[pos + 8 : pos + 8 + tamanho]
        if len(corpo) < tamanho:
            raise ValueError("WAV truncado")
        if chunk == b"fmt " and tamanho >= 16:
            formato, canais, taxa, _, _, bits = struct.unpack("<HHIIHH", corpo[:16])
        elif chunk == b"data":
            amostras = corpo
        pos += 8 + tamanho + (tamanho % 2)  # chunk impar tem 1 byte de padding

    if amostras is None or taxa <= 0 or canais <= 0:
        raise ValueError("WAV sem chunk fmt/data")
    if formato == 3 and bits == 32:
        audio = np.frombuffer(
            amostras[: len(amostras) - len(amostras) % 4], dtype="<f4"
        ).astype(np.float32)
    elif formato == 1 and bits == 16:
        cru = np.frombuffer(amostras[: len(amostras) - len(amostras) % 2], dtype="<i2")
        audio = cru.astype(np.float32) / 32768.0
    elif formato == 1 and bits == 8:
        cru8 = np.frombuffer(amostras, dtype=np.uint8)
        audio = (cru8.astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"WAV nao suportado (formato {formato}, {bits} bits)")

    if canais > 1:
        sobra = len(audio) % canais
        if sobra:
            audio = audio[:-sobra]
        audio = audio.reshape(-1, canais).mean(axis=1)
    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    audio = np.clip(audio.astype(np.float32), -1.0, 1.0)
    if len(audio) == 0:
        return audio
    if taxa != SAMPLE_RATE:
        destino = max(1, round(len(audio) * SAMPLE_RATE / taxa))
        audio = np.interp(
            np.linspace(0.0, len(audio) - 1.0, destino),
            np.arange(len(audio), dtype=np.float64),
            audio.astype(np.float64),
        ).astype(np.float32)
    return audio


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
        # Uma transcricao por vez: duas abas enviando WAV ao mesmo tempo
        # disputariam os nucleos e as duas sairiam mais lentas.
        self._upload_lock = asyncio.Lock()
        # Fase da escuta, publicada em `GET /voice`. A tela pergunta para trocar
        # o botao de "ouvindo" para "transcrevendo" no instante em que o VAD
        # fecha a fala — o POST /voice/listen so responde no fim.
        self.state = STATE_IDLE

    # ------------------------------------------------------------- microfone
    def available(self) -> tuple[bool, str]:
        """Microfone e transcricao prontos? (devolve a razao quando nao)."""
        from gaia.audio.recorder import Recorder

        if self.recorder is None:
            return False, "este motor nao tem microfone"
        ok, motivo = self.transcription_available()
        if not ok:
            return ok, motivo
        try:
            return Recorder.available()
        except Exception as exc:  # pragma: no cover - depende do sistema
            return False, str(exc)

    def transcription_available(self) -> tuple[bool, str]:
        """O STT esta pronto? (o upload do navegador nao precisa de microfone)."""
        if self.transcriber is None:
            return False, "transcricao indisponivel neste motor"
        return True, ""

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
            self.state = STATE_LISTENING
            try:
                audio = await asyncio.to_thread(self._record, clamp_window(window))
                if audio is None or len(audio) == 0:
                    return {"text": "", "heard": False}
                self.state = STATE_TRANSCRIBING
                texto = await asyncio.to_thread(self._transcribe, audio)
                return {"text": texto, "heard": bool(texto)}
            finally:
                self.state = STATE_IDLE

    async def upload(self, wav_bytes: bytes) -> dict[str, Any]:
        """Transcreve o WAV que veio do microfone do navegador.

        Mesmo contrato de ``listen``: ``heard=False`` para audio vazio ou
        calado. Nada e gravado em disco e o buffer e descartado ao fim.
        ``ValueError`` (WAV invalido/longo demais) e do transporte: vira 400,
        nunca 500.
        """
        ok, motivo = self.transcription_available()
        if not ok:
            raise VoiceUnavailable(motivo)
        if not wav_bytes:
            return {"text": "", "heard": False}
        if self._upload_lock.locked():
            raise VoiceUnavailable("ja existe uma transcricao em andamento")
        audio = decode_wav(wav_bytes)
        if len(audio) == 0:
            return {"text": "", "heard": False}
        if len(audio) > int(MAX_UPLOAD_SECONDS * SAMPLE_RATE):
            raise ValueError(
                f"audio longo demais (maximo de {int(MAX_UPLOAD_SECONDS)} s)"
            )
        async with self._upload_lock:
            anterior = self.state
            self.state = STATE_TRANSCRIBING
            try:
                texto = await asyncio.to_thread(self._transcribe, audio)
                return {"text": texto, "heard": bool(texto)}
            finally:
                # Nao pisa na fase de uma escuta do servidor em andamento.
                self.state = anterior if anterior == STATE_LISTENING else STATE_IDLE

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
    "MAX_UPLOAD_SECONDS",
    "MAX_WINDOW_SECONDS",
    "MIN_WINDOW_SECONDS",
    "SAMPLE_RATE",
    "STATE_IDLE",
    "STATE_LISTENING",
    "STATE_TRANSCRIBING",
    "VoiceUnavailable",
    "WebVoice",
    "clamp_window",
    "content_type",
    "decode_wav",
]
