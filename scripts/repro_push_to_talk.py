"""Reproduz o caminho do atalho 'f' (falar) da TUI, sem depender do agente.

Uso: rode em um terminal e, enquanto ele estiver "ouvindo", fale algo.
"""

from __future__ import annotations

import asyncio
import types

from gaia.audio.recorder import Recorder
from gaia.config import AudioConfig, STTConfig
from gaia.interface.textual_app import GaiaTextualApp
from gaia.stt.faster_whisper import WhisperTranscriber

STATUS: list[str] = []
SUBMITTED: list[str] = []


class FakeUI:
    permission_callback = None


class SpyRecorder(Recorder):
    def record_until_silence(self, on_state=None, wait_timeout=None):
        audio = super().record_until_silence(on_state, wait_timeout)
        import numpy as np

        np.save("/tmp/repro_audio.npy", audio)
        print(f"AUDIO dur={len(audio) / 16000:.2f}s peak={float(np.max(np.abs(audio))):.4f}", flush=True)
        return audio


class SpyTranscriber(WhisperTranscriber):
    def transcribe(self, audio, vad_filter=None):
        try:
            text = super().transcribe(audio, vad_filter)
        except Exception as exc:
            print(f"TRANSCREVER FALHOU: {type(exc).__name__}: {exc}", flush=True)
            raise
        print(f"TRANSCREVEU -> {text!r}", flush=True)
        return text


class FakeVoice:
    def __init__(self) -> None:
        self.recorder = SpyRecorder(AudioConfig())
        self.transcriber = SpyTranscriber(STTConfig(model="base"))
        self.ui = FakeUI()
        self.cfg = types.SimpleNamespace(audio=AudioConfig())
        self.session_id = "ses_teste"
        self.mode_key = "assistente"
        self.mode_name = "Assistente"


async def answer(text: str, sink) -> str:
    SUBMITTED.append(text)
    return f"(recebido: {text})"


async def main() -> None:
    app = GaiaTextualApp(answer, voice=FakeVoice())
    original = app._set_status

    def spy(text: str) -> None:
        STATUS.append(text)
        original(text)

    app._set_status = spy  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        print("pressione f e fale...", flush=True)
        await pilot.press("f")
        for _ in range(120):
            await asyncio.sleep(0.5)
            if SUBMITTED:
                break
        print("STATUS:", STATUS, flush=True)
        print("SUBMETIDO:", SUBMITTED, flush=True)


asyncio.run(main())
