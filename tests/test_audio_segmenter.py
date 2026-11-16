from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from zaya.audio.recorder import SileroVAD, UtteranceSegmenter
from zaya.config import AudioConfig

SR = 16000
BLOCK = int(SR * 0.03)  # 30ms
SAMPLE = Path(__file__).parent / "fixtures" / "vad_ruido_e_fala.npz"

# Trechos do fixture (segundos): ruido grave, fala e silencio de novo.
RUIDO_FIM = 1.5
FALA_INICIO = 1.5
FALA_FIM = 5.0
SILENCIO_INICIO = 5.3


def config(**overrides) -> AudioConfig:
    base = {
        "sample_rate": SR,
        "silence_ms": 180,
        "min_speech_ms": 90,
        "preroll_ms": 90,
        "max_seconds": 0.0,
        "speech_threshold_db": -40.0,
    }
    base.update(overrides)
    return AudioConfig(**base)


def frame(amplitude: float) -> np.ndarray:
    return np.full(BLOCK, amplitude, dtype=np.float32)


def rms_segmenter(**overrides) -> UtteranceSegmenter:
    """Segmentador no modo reserva (RMS), sem depender do modelo Silero."""
    return UtteranceSegmenter(config(**overrides), use_vad=False)


def load_sample() -> np.ndarray:
    """Ruido de ar-condicionado, fala, silencio: gravado do microfone real."""
    with np.load(SAMPLE) as data:
        audio = data["audio"].astype(np.float32) / 32767.0
    return audio


def test_ends_after_silence():
    segmenter = rms_segmenter()
    for _ in range(3):
        assert segmenter.feed(frame(1e-4)) is None
    for _ in range(10):
        assert segmenter.feed(frame(0.2)) is None

    utterance = None
    for _ in range(30):
        utterance = segmenter.feed(frame(1e-4))
        if utterance is not None:
            break
    assert utterance is not None
    assert len(utterance) >= BLOCK * 10
    assert segmenter.speech_started is False


def test_max_seconds_emits_without_silence():
    segmenter = rms_segmenter(max_seconds=0.2)
    utterance = None
    for _ in range(20):
        utterance = segmenter.feed(frame(0.2))
        if utterance is not None:
            break
    assert utterance is not None


def test_no_speech_returns_nothing():
    segmenter = rms_segmenter()
    for _ in range(20):
        assert segmenter.feed(frame(1e-4)) is None
    assert segmenter.flush() is None


def test_snapshot_returns_captured_audio():
    segmenter = rms_segmenter()
    segmenter.start_manual()
    for _ in range(5):
        segmenter.feed(frame(0.2))
    snapshot = segmenter.snapshot()
    assert snapshot is not None
    assert len(snapshot) >= BLOCK * 5

    tail = segmenter.snapshot(max_seconds=0.06)
    assert tail is not None
    assert len(tail) <= BLOCK * 2


def test_manual_start_and_flush():
    segmenter = rms_segmenter()
    segmenter.feed(frame(1e-4))
    segmenter.start_manual()
    assert segmenter.speech_started is True
    for _ in range(5):
        segmenter.feed(frame(0.2))
    utterance = segmenter.flush()
    assert utterance is not None
    assert len(utterance) > 0
    assert segmenter.speech_started is False


# --------------------------------------------------------------------- Silero


def silero_vad() -> SileroVAD:
    vad = SileroVAD(SR)
    if not vad.enabled:  # pragma: no cover - ambiente sem onnxruntime
        pytest.skip("onnxruntime indisponivel")
    return vad


def test_silero_separa_ruido_grave_de_fala():
    """O ruido de ar-condicionado nao pode ser confundido com voz."""
    vad = silero_vad()
    audio = load_sample()
    probabilities = np.array(
        [vad.probability(audio[i : i + BLOCK]) for i in range(0, len(audio) - BLOCK, BLOCK)]
    )
    por_segundo = SR / BLOCK

    ruido = probabilities[: int(RUIDO_FIM * por_segundo)]
    fala = probabilities[int(FALA_INICIO * por_segundo) : int(FALA_FIM * por_segundo)]
    silencio = probabilities[int(SILENCIO_INICIO * por_segundo) :]

    assert (ruido >= 0.5).sum() == 0
    assert (fala >= 0.5).mean() >= 0.85
    assert (silencio >= 0.5).sum() == 0


def test_segmenter_fecha_a_fala_no_ruido_de_ar_condicionado():
    """Regressao: ruido grave prendia a escuta em "ouvindo" para sempre."""
    segmenter = UtteranceSegmenter(config(silence_ms=1500))
    if not segmenter._vad.enabled:  # pragma: no cover - ambiente sem onnxruntime
        pytest.skip("onnxruntime indisponivel")

    audio = load_sample()
    inicio = None
    for index in range(0, len(audio) - BLOCK, BLOCK):
        posicao = index / SR
        utterance = segmenter.feed(audio[index : index + BLOCK])
        if inicio is None and segmenter.speech_started:
            inicio = posicao
        if utterance is not None:
            fim = posicao
            break
    else:
        raise AssertionError("a fala nunca terminou")

    assert inicio is not None and abs(inicio - FALA_INICIO) < 0.4
    assert fim > FALA_FIM, "cortou a fala antes do fim"
    # O audio entregue ao STT tem a fala inteira, nao so o rufo de fundo.
    assert len(utterance) / SR >= 3.0
    assert segmenter.speech_started is False


def test_estalo_isolado_nao_abre_fala():
    segmenter = UtteranceSegmenter(config())
    if not segmenter._vad.enabled:  # pragma: no cover - ambiente sem onnxruntime
        pytest.skip("onnxruntime indisponivel")

    audio = load_sample()
    ruido = audio[: int(RUIDO_FIM * SR)]
    estalo = audio[int(2.5 * SR) : int(2.5 * SR) + BLOCK]
    for index in range(0, len(ruido) - BLOCK, BLOCK):
        assert segmenter.feed(ruido[index : index + BLOCK]) is None
    segmenter.feed(estalo)
    for index in range(0, len(ruido) - BLOCK, BLOCK):
        assert segmenter.feed(ruido[index : index + BLOCK]) is None
    assert segmenter.speech_started is False


def test_estalo_curto_nao_deixa_a_escuta_travada():
    """Regressao: um estalo abria a fala e, sem fala suficiente, ela nunca fechava."""
    segmenter = rms_segmenter()
    assert segmenter.feed(frame(0.2)) is None  # estalo de 30 ms abre a escuta
    assert segmenter.speech_started is True

    for _ in range(20):
        assert segmenter.feed(frame(1e-4)) is None
    # O estalo foi descartado em vez de prender a escuta para sempre.
    assert segmenter.speech_started is False

    # E a proxima fala de verdade ainda e ouvida normalmente.
    for _ in range(5):
        segmenter.feed(frame(0.2))
    utterance = None
    for _ in range(20):
        utterance = segmenter.feed(frame(1e-4))
        if utterance is not None:
            break
    assert utterance is not None
    assert segmenter.speech_started is False


class _VadCurto:
    """VAD falso: confirma fala por N blocos e depois fica em silencio."""

    enabled = True

    def __init__(self, blocos: int) -> None:
        self.blocos = blocos
        self.vistos = 0

    def reset(self) -> None:
        # O estalo acontece uma vez so: resetar o segmentador nao o reacende.
        pass

    def probability(self, block):
        self.vistos += 1
        return 0.9 if self.vistos <= self.blocos else 0.0


def test_estalo_com_silero_nao_trava_e_volta_a_ouvir():
    """Mesmo caminho do VAD real: estalo abre, silencio fecha, e depois reabre."""
    segmenter = UtteranceSegmenter(config(silence_ms=300, min_speech_ms=150))
    segmenter._vad = _VadCurto(blocos=3)

    for _ in range(30):
        assert segmenter.feed(frame(1e-4)) is None
    assert segmenter.speech_started is False, "o estalo nao pode prender a escuta"
    assert segmenter.snapshot() is None, "o audio do estalo nao pode ser acumulado"

    # Uma fala de verdade depois do estalo e entregue inteira.
    segmenter._vad = _VadCurto(blocos=10)
    utterance = None
    for _ in range(40):
        utterance = segmenter.feed(frame(1e-4))
        if utterance is not None:
            break
    assert utterance is not None
    assert len(utterance) >= BLOCK * 10
    assert segmenter.speech_started is False


def test_reserva_rms_continua_funcionando():
    """Sem onnxruntime o segmentador ainda precisa detectar fala."""
    segmenter = UtteranceSegmenter(config(silence_ms=150), use_vad=False)
    for _ in range(10):
        segmenter.feed(frame(0.2))
    utterance = None
    for _ in range(10):
        utterance = segmenter.feed(frame(1e-4))
        if utterance is not None:
            break
    assert utterance is not None
