"""Captura de microfone: push-to-talk e escuta continua com VAD.

Depende de sounddevice (PortAudio). Nenhum audio sai da maquina: a captura
fica em memoria e e entregue ao STT local.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from zaya.config import AudioConfig

logger = logging.getLogger(__name__)

# Silero v6: janela fixa de 512 amostras (32 ms @ 16 kHz) e contexto de 64.
_VAD_WINDOW = 512
_VAD_CONTEXT = 64
_vad_session = None
_vad_session_tried = False


class AudioError(RuntimeError):
    pass


def _load_vad_session():
    """Sessao onnxruntime do Silero, carregada uma vez e compartilhada.

    O `faster_whisper.vad.get_vad_model()` zera `h`/`c` a cada chamada, o que
    destroi o estado recorrente e faz o modelo responder lixo quando chamado
    bloco a bloco. Aqui a sessao e crua e o estado fica com quem chama.
    """
    global _vad_session, _vad_session_tried
    if _vad_session_tried:
        return _vad_session
    _vad_session_tried = True
    try:
        import onnxruntime
        from faster_whisper.utils import get_assets_path
    except Exception as exc:  # pragma: no cover - depende do ambiente
        # Um VAD desligado faz o RMS voltar a confundir ruido grave com fala:
        # o bug volta em silencio. Por isso o aviso e warning, nao debug.
        logger.warning("Silero VAD indisponivel; caindo no RMS: %s", exc)
        return None
    path = os.path.join(get_assets_path(), "silero_vad_v6.onnx")
    if not os.path.exists(path):  # pragma: no cover - depende do ambiente
        logger.warning("modelo do Silero VAD nao encontrado em %s; usando o RMS", path)
        return None
    opts = onnxruntime.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    opts.enable_cpu_mem_arena = False
    opts.log_severity_level = 4
    try:
        _vad_session = onnxruntime.InferenceSession(
            path, providers=["CPUExecutionProvider"], sess_options=opts
        )
    except Exception as exc:  # pragma: no cover - depende do ambiente
        logger.warning("falha ao carregar o Silero VAD; usando o RMS: %s", exc)
        _vad_session = None
    return _vad_session


class SileroVAD:
    """Probabilidade de fala por bloco, com estado recorrente persistente.

    O RMS bruto nao separa fala de ruido de fundo: um ventilador ou ar
    condicionado em 58-200 Hz domina o nivel e deixa o microfone "sempre
    falando", entao a pausa nunca e detectada e a escuta continua trava. O
    Silero olha o espectro da fala (300-4000 Hz) e ignora esse ronco.

    Aceita blocos de qualquer tamanho: o que sobra fica num buffer ate fechar
    uma janela de 512 amostras.
    """

    def __init__(self, sample_rate: int = 16000, enabled: bool = True) -> None:
        self._session = _load_vad_session() if enabled and sample_rate == 16000 else None
        self.enabled = self._session is not None
        if enabled and not self.enabled:
            logger.warning("Silero VAD desativado; usando o RMS como reserva")
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _VAD_CONTEXT), dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._last = 0.0

    def probability(self, block: np.ndarray) -> float:
        """Probabilidade (0..1) de haver fala no bloco recebido."""
        session = self._session
        if session is None:
            return 0.0
        data = np.concatenate([self._pending, np.asarray(block, dtype=np.float32).reshape(-1)])
        windows: list[np.ndarray] = []
        while data.size >= _VAD_WINDOW:
            windows.append(data[:_VAD_WINDOW])
            data = data[_VAD_WINDOW:]
        self._pending = data.astype(np.float32, copy=True)
        if not windows:
            return self._last

        # Um bloco de audio pode conter mais de uma janela: vale a maior.
        best = 0.0
        for window in windows:
            frame = window.reshape(1, -1)
            inputs = np.concatenate([self._context, frame], axis=1)
            out, self._h, self._c = session.run(None, {"input": inputs, "h": self._h, "c": self._c})
            self._context = frame[:, -_VAD_CONTEXT:]
            best = max(best, float(np.ravel(out)[0]))
        self._last = best
        return best


def resolve_input_device(sd, raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    needle = raw.lower()
    for index, device in enumerate(sd.query_devices()):
        if device.get("max_input_channels", 0) <= 0:
            continue
        if needle in device.get("name", "").lower():
            return index
    raise AudioError(f"dispositivo de entrada '{raw}' nao encontrado")


class UtteranceSegmenter:
    """Transforma um fluxo continuo de blocos de audio em falas completas.

    Regras: espera fala, inclui preroll, encerra depois de `silence_ms` de
    silencio ou ao atingir `max_seconds`. Fala curta demais (menos de
    `min_speech_ms` de voz) e descartada como estalo, nunca acumulada para
    sempre.

    Quem decide "tem fala agora?" e o Silero VAD (probabilidade por bloco, com
    histerese). O RMS contra `speech_threshold_db` fica so como reserva, para
    ambientes sem onnxruntime ou com taxa diferente de 16 kHz.
    """

    # Blocos consecutivos acima do limiar para confirmar o inicio da fala
    # (90 ms a 30 ms/bloco): evita que um estalo isolado abra uma fala.
    START_WINDOWS = 3
    # Limiar menor para continuar: na duvida, nao corta a fala no meio.
    CONTINUE_THRESHOLD = 0.35

    def __init__(self, cfg: AudioConfig, use_vad: bool = True):
        self.cfg = cfg
        self._preroll_max = max(1, int(cfg.preroll_ms / 30.0))
        self._vad = SileroVAD(cfg.sample_rate, enabled=use_vad)
        self.reset()

    @property
    def speech_started(self) -> bool:
        return self._speech_started

    def reset(self) -> None:
        self._preroll: deque[np.ndarray] = deque(maxlen=self._preroll_max)
        self._chunks: list[np.ndarray] = []
        self._speech_started = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._utterance_ms = 0.0
        self._state_sent = False
        self._start_run = 0
        if self._vad.enabled:
            self._vad.reset()

    def _is_voiced(self, block: np.ndarray) -> bool:
        """Fala agora? Silero quando disponivel, RMS como reserva."""
        if self._vad.enabled:
            threshold = float(getattr(self.cfg, "vad_threshold", 0.5))
            probability = self._vad.probability(block)
            if self._speech_started:
                return probability >= min(self.CONTINUE_THRESHOLD, threshold)
            if probability >= threshold:
                self._start_run += 1
            else:
                self._start_run = 0
            return self._start_run >= self.START_WINDOWS

        self._start_run = 0
        rms = float(np.sqrt(np.mean(np.square(block)) + 1e-12))
        db = 20.0 * float(np.log10(rms + 1e-9))
        return db > float(getattr(self.cfg, "speech_threshold_db", -42.0))

    def feed(
        self,
        data: np.ndarray,
        on_state: Callable[[str], None] | None = None,
    ) -> np.ndarray | None:
        if data is None or data.size == 0:
            return None
        cfg = self.cfg
        block = data.astype(np.float32, copy=False).reshape(-1)
        block_ms = len(block) / cfg.sample_rate * 1000.0

        voiced = self._is_voiced(block)

        if voiced and not self._speech_started:
            self._speech_started = True
            if self._preroll:
                self._chunks.extend(self._preroll)
                self._preroll.clear()
            if on_state and not self._state_sent:
                on_state("ouvindo")
                self._state_sent = True

        if not self._speech_started:
            self._preroll.append(block)
            return None

        self._chunks.append(block)
        self._utterance_ms += block_ms
        if voiced:
            self._speech_ms += block_ms
            self._silence_ms = 0.0
        else:
            self._silence_ms += block_ms

        if self._silence_ms >= cfg.silence_ms:
            if self._speech_ms >= cfg.min_speech_ms:
                return self._emit()
            # Passou o tempo de silencio sem fala suficiente: o que abriu a
            # escuta foi um estalo. Descarta e volta a esperar; sem isso o
            # segmentador fica preso em `speech_started` para sempre.
            self.reset()
            return None

        too_long = cfg.max_seconds > 0 and self._utterance_ms >= cfg.max_seconds * 1000.0
        if too_long:
            if self._speech_ms >= cfg.min_speech_ms:
                return self._emit()
            self.reset()
        return None

    def start_manual(self) -> None:
        """Inicia uma fala manualmente (Enter), mesmo antes de detectar voz."""
        if self._speech_started:
            return
        self._speech_started = True
        if self._preroll:
            self._chunks.extend(self._preroll)
            self._preroll.clear()

    def snapshot(self, max_seconds: float | None = None) -> np.ndarray | None:
        """Audio capturado ate agora (para transcricao parcial ao vivo)."""
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks).astype(np.float32, copy=False)
        if max_seconds and max_seconds > 0:
            limit = int(self.cfg.sample_rate * max_seconds)
            if len(audio) > limit:
                audio = audio[-limit:]
        return audio

    def flush(self) -> np.ndarray | None:
        if self._speech_started and self._speech_ms >= self.cfg.min_speech_ms:
            return self._emit()
        self.reset()
        return None

    def _emit(self) -> np.ndarray | None:
        if not self._chunks:
            self.reset()
            return None
        audio = np.concatenate(self._chunks).astype(np.float32, copy=False)
        self.reset()
        return audio


class Recorder:
    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ checks
    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - depende do sistema
            return False, f"sounddevice indisponivel: {exc}"
        try:
            devices = sd.query_devices()
            inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
            if not inputs:
                return False, "nenhum dispositivo de entrada encontrado"
            return True, f"{len(inputs)} dispositivo(s) de entrada"
        except Exception as exc:  # pragma: no cover
            return False, f"PortAudio indisponivel: {exc}"

    # ----------------------------------------------------------------- record
    def record_until_silence(
        self,
        on_state: Callable[[str], None] | None = None,
        wait_timeout: float | None = None,
    ) -> np.ndarray:
        """Grava uma fala isolada (push-to-talk). Retorna float32 mono @16k."""
        self._stop_event.clear()
        try:
            import sounddevice as sd
        except Exception as exc:
            raise AudioError(f"sounddevice indisponivel: {exc}") from exc

        cfg = self.cfg
        sr = cfg.sample_rate
        block = max(1, int(sr * 0.03))
        device = resolve_input_device(sd, cfg.input_device)
        frames: queue.Queue[np.ndarray] = queue.Queue()

        def callback(indata, frames_count, time_info, status):
            frames.put(indata.copy().reshape(-1))

        segmenter = UtteranceSegmenter(cfg)
        if on_state:
            on_state("aguardando fala")

        try:
            stream = sd.InputStream(
                samplerate=sr,
                channels=cfg.channels,
                dtype="float32",
                blocksize=block,
                device=device,
                callback=callback,
            )
        except Exception as exc:
            raise AudioError(f"nao foi possivel abrir o microfone: {exc}") from exc

        with stream:
            started_at = time.monotonic()
            deadline = started_at + cfg.max_seconds if cfg.max_seconds > 0 else None
            while deadline is None or time.monotonic() < deadline:
                if self._stop_event.is_set():
                    break
                if (
                    not segmenter.speech_started
                    and wait_timeout is not None
                    and time.monotonic() - started_at >= wait_timeout
                ):
                    break
                try:
                    data = frames.get(timeout=0.2)
                except queue.Empty:
                    continue
                utterance = segmenter.feed(data, on_state)
                if utterance is not None:
                    return utterance

        flushed = segmenter.flush()
        return flushed if flushed is not None else np.zeros(0, dtype=np.float32)


class ContinuousListener:
    """Mantem o microfone aberto e entrega falas segmentadas por VAD."""

    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._frames: queue.Queue[np.ndarray] = queue.Queue()
        self._utterances: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._segmenter = UtteranceSegmenter(cfg)
        self._seg_lock = threading.Lock()

    def start(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            raise AudioError(f"sounddevice indisponivel: {exc}") from exc

        cfg = self.cfg
        block = max(1, int(cfg.sample_rate * 0.03))
        device = resolve_input_device(sd, cfg.input_device)

        def callback(indata, frames_count, time_info, status):
            if self._stop.is_set() or self._paused.is_set():
                return
            self._frames.put(indata.copy().reshape(-1))

        try:
            stream = sd.InputStream(
                samplerate=cfg.sample_rate,
                channels=cfg.channels,
                dtype="float32",
                blocksize=block,
                device=device,
                callback=callback,
            )
            stream.start()
            self._stream = stream
        except Exception as exc:
            raise AudioError(f"nao foi possivel abrir o microfone: {exc}") from exc

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._paused.is_set():
                continue
            with self._seg_lock:
                utterance = self._segmenter.feed(data)
            if utterance is not None and len(utterance) > 0:
                self._utterances.put(utterance)
        self._utterances.put(None)

    def next_utterance(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self._utterances.get(timeout=timeout)
        except queue.Empty:
            return None

    def is_capturing(self) -> bool:
        with self._seg_lock:
            return self._segmenter.speech_started

    def snapshot(self, max_seconds: float | None = None) -> np.ndarray | None:
        with self._seg_lock:
            return self._segmenter.snapshot(max_seconds)

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def force_start(self) -> bool:
        """Enter: inicia uma fala manual (sem esperar o VAD detectar voz)."""
        if self._paused.is_set():
            return False
        with self._seg_lock:
            if self._segmenter.speech_started:
                return False
            self._segmenter.start_manual()
        return True

    def force_end(self) -> bool:
        """Enter: encerra a fala atual imediatamente e a envia para transcricao."""
        with self._seg_lock:
            utterance = self._segmenter.flush()
        if utterance is not None and len(utterance) > 0:
            self._utterances.put(utterance)
            return True
        return False

    def pause(self) -> None:
        self._paused.set()
        self._drain_frames()
        with self._seg_lock:
            self._segmenter.reset()

    def resume(self) -> None:
        with self._seg_lock:
            self._segmenter.reset()
        self._drain_frames()
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.debug("falha ao fechar o stream de audio: %s", exc)
            self._stream = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._utterances.put(None)

    def _drain_frames(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return
