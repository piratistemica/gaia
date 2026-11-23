"""Modo voz do chat web: microfone no motor, transcricao local e audio no navegador.

Tres contratos que a tela depende e que quebram em silencio:

* ``GET /voice`` so diz ``available`` quando ha microfone **e** STT nesta
  maquina (o botao nao aparece num mini sem entrada de audio);
* ``POST /voice/listen`` devolve o texto transcrito (ou ``heard=false`` para
  silencio) e uma segunda escuta simultanea recebe 503, nunca dois streams;
* ``POST /voice/speak`` devolve o mp3/aiff do mesmo TTS da TUI em base64, e
  ``/voice/stop`` solta a janela em curso sem erro.

O microfone e o do **servidor** de proposito: no mini headless nao existe caixa
de som, mas existe o faster-whisper. Quem reproduz e o navegador de quem
conversa. Nada aqui toca a rede de audio nem grava em disco depois de lido.
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gaia.audio.recorder import AudioError
from gaia.interface.remote import RemoteUI
from gaia.serve.app import OrchestratorTarget
from gaia.serve.server import ServeServer
from gaia.serve.voice import (
    LISTEN_WINDOW_SECONDS,
    MAX_WINDOW_SECONDS,
    MIN_WINDOW_SECONDS,
    VoiceUnavailable,
    WebVoice,
    clamp_window,
)

# --------------------------------------------------------------- unidade ---


class RecorderFake:
    """Microfone falso: devolve o audio configurado ou levanta AudioError."""

    def __init__(self, audio: Any = None, erro: Exception | None = None) -> None:
        self.audio = audio
        self.erro = erro
        self.janelas: list[float] = []
        self.paradas = 0

    def record_until_silence(self, wait_timeout: float | None = None) -> Any:
        self.janelas.append(wait_timeout or 0.0)
        if self.erro is not None:
            raise self.erro
        return self.audio

    def stop(self) -> None:
        self.paradas += 1


class Transcriber:
    def __init__(self, texto: str = "bom dia", erro: Exception | None = None) -> None:
        self.texto = texto
        self.erro = erro
        self.chamadas = 0

    def transcribe(self, audio: Any) -> str:
        self.chamadas += 1
        if self.erro is not None:
            raise self.erro
        return self.texto


class Backend:
    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        self.limpou: list[Path] = []

    def cleanup(self, caminho: Path) -> None:
        self.limpou.append(caminho)
        caminho.unlink(missing_ok=True)


class TTS:
    enabled = True

    def __init__(self, dados: bytes = b"ID3audio", erro: Exception | None = None) -> None:
        self.dados = dados
        self.erro = erro
        self.textos: list[str] = []

    async def prepare(self, texto: str) -> tuple[Backend, Path] | None:
        self.textos.append(texto)
        if self.erro is not None:
            raise self.erro
        caminho = Path(self._tmp())
        caminho.write_bytes(self.dados)
        return Backend(caminho), caminho

    @staticmethod
    def _tmp() -> str:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            return fh.name


def _audio(segundos: float = 1.0) -> np.ndarray:
    return np.zeros(int(16000 * segundos), dtype=np.float32)


def _wav(
    audio: np.ndarray | None = None,
    *,
    taxa: int = 16000,
    canais: int = 1,
    bits: int = 16,
    com_lista: bool = False,
) -> bytes:
    """Monta um WAV de verdade (como o navegador envia) para os testes."""
    import struct

    if audio is None:
        t = np.linspace(0, 0.5, int(taxa * 0.5), endpoint=False)
        audio = np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.5
    if bits == 16:
        cru = (audio * 32767).astype("<i2").tobytes()
        formato, alinhamento = 1, canais * 2
    elif bits == 8:
        cru = ((audio * 127) + 128).astype(np.uint8).tobytes()
        formato, alinhamento = 1, canais
    else:
        cru = audio.astype("<f4").tobytes()
        formato, alinhamento = 3, canais * 4
    fmt = struct.pack("<HHIIHH", formato, canais, taxa, taxa * alinhamento, alinhamento, bits)
    # Um chunk extra antes do `data` exercita a varredura (o Chrome grava LIST).
    extra = b"LIST" + struct.pack("<I", 4) + b"INFO" if com_lista else b""
    corpo = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra
    corpo += b"data" + struct.pack("<I", len(cru)) + cru
    return b"RIFF" + struct.pack("<I", len(corpo)) + corpo


def test_clamp_window_normaliza_janela():
    assert clamp_window(15) == 15.0
    assert clamp_window(0) == LISTEN_WINDOW_SECONDS
    assert clamp_window(-3) == LISTEN_WINDOW_SECONDS
    assert clamp_window("abc") == LISTEN_WINDOW_SECONDS
    assert clamp_window(1) == MIN_WINDOW_SECONDS
    assert clamp_window(999) == MAX_WINDOW_SECONDS


def test_listen_devolve_texto_e_usa_a_janela():
    gravador = RecorderFake(audio=_audio())
    voz = WebVoice(gravador, Transcriber("bom dia"), TTS())
    dados = asyncio.run(voz.listen(9))
    assert dados == {"text": "bom dia", "heard": True}
    assert gravador.janelas == [9.0]


def test_listen_sem_fala_nao_e_erro():
    voz = WebVoice(RecorderFake(audio=np.zeros(0, dtype=np.float32)), Transcriber(), TTS())
    dados = asyncio.run(voz.listen(15))
    assert dados == {"text": "", "heard": False}


def test_listen_sem_microfone_levanta_unavailable():
    voz = WebVoice(RecorderFake(erro=AudioError("abrir microfone falhou")), Transcriber(), TTS())
    with pytest.raises(VoiceUnavailable):
        asyncio.run(voz.listen(15))


def test_listen_sem_stt_levanta_unavailable():
    voz = WebVoice(RecorderFake(audio=_audio()), None, TTS())
    with pytest.raises(VoiceUnavailable):
        asyncio.run(voz.listen(15))


def test_speech_devolve_bytes_e_apaga_o_tmp():
    tts = TTS(dados=b"mp3-fake")
    voz = WebVoice(RecorderFake(), Transcriber(), tts)
    preparado = asyncio.run(voz.speech("oi"))
    assert preparado is not None
    dados, tipo = preparado
    assert dados == b"mp3-fake"
    assert tipo == "audio/mpeg"
    assert tts.textos == ["oi"]


def test_speech_sem_tts_habilitado_devolve_none():
    class Desligado:
        enabled = False

    voz = WebVoice(RecorderFake(), Transcriber(), Desligado())
    assert asyncio.run(voz.speech("oi")) is None


def test_speech_com_tts_quebrado_nao_estoura():
    voz = WebVoice(RecorderFake(), Transcriber(), TTS(erro=RuntimeError("sem rede")))
    assert asyncio.run(voz.speech("oi")) is None


def test_disponivel_usa_o_recorder_de_verdade(monkeypatch):
    monkeypatch.setattr(
        "gaia.audio.recorder.Recorder.available", staticmethod(lambda: (False, "sem mic"))
    )
    voz = WebVoice(RecorderFake(), Transcriber(), TTS())
    assert voz.available() == (False, "sem mic")


def test_janela_em_curso_recusa_segunda_escuta():
    class Lento(RecorderFake):
        def record_until_silence(self, wait_timeout: float | None = None) -> Any:
            time.sleep(0.2)  # ocupa o lock o bastante para a corrida
            return super().record_until_silence(wait_timeout)

    voz = WebVoice(Lento(audio=_audio(0.2)), Transcriber(), TTS())

    async def corrida() -> tuple[dict[str, Any], str]:
        primeiro = asyncio.create_task(voz.listen(1.0))
        await asyncio.sleep(0.05)  # a primeira escuta ja esta segurando o lock
        try:
            await voz.listen(1.0)
            erro = ""
        except VoiceUnavailable as exc:
            erro = str(exc)
        return await primeiro, erro

    dados, erro = asyncio.run(corrida())
    assert dados["text"] == "bom dia"
    assert "andamento" in erro


# ------------------------------------------------------- WAV do navegador ---


def test_decode_wav_16k_mono_devolve_float32_no_tamanho_certo():
    from gaia.serve.voice import decode_wav

    audio = decode_wav(_wav())
    assert audio.dtype == np.float32
    assert len(audio) == 8000  # 0,5 s a 16 kHz
    assert -1.0 <= float(audio.min()) and float(audio.max()) <= 1.0


def test_decode_wav_reamostra_44k_para_16k():
    from gaia.serve.voice import decode_wav

    audio = decode_wav(_wav(taxa=44100))
    assert abs(len(audio) - 8000) <= 2  # 0,5 s a 16 kHz


def test_decode_wav_estereo_vira_mono():
    from gaia.serve.voice import decode_wav

    amostras = np.tile(np.array([0.5, -0.5], dtype=np.float32), 8000)
    audio = decode_wav(_wav(amostras, canais=2))
    assert len(audio) == 8000
    assert float(np.abs(audio).max()) < 1e-3  # media de +0,5 e -0,5 = 0


def test_decode_wav_pula_chunk_antes_do_data():
    from gaia.serve.voice import decode_wav

    assert len(decode_wav(_wav(com_lista=True))) == 8000


def test_decode_wav_aceita_float32_e_8_bits():
    from gaia.serve.voice import decode_wav

    assert decode_wav(_wav(audio=np.full(8000, 0.5, dtype=np.float32), bits=32)).dtype == np.float32
    assert len(decode_wav(_wav(audio=np.zeros(8000, dtype=np.float32), bits=8))) == 8000


def test_decode_wav_recusa_o_que_nao_e_wav():
    from gaia.serve.voice import decode_wav

    with pytest.raises(ValueError):
        decode_wav(b"OggS\x00\x02 nao e wav")
    with pytest.raises(ValueError):
        decode_wav(b"RIFF\x00\x00\x00\x00WAVE" + b"junk")  # sem fmt/data


def test_upload_transcreve_o_wav_do_navegador():
    voz = WebVoice(RecorderFake(), Transcriber("duas da tarde"), TTS())
    dados = asyncio.run(voz.upload(_wav()))
    assert dados == {"text": "duas da tarde", "heard": True}
    assert voz.state == "idle"


def test_upload_sem_fala_nao_e_erro():
    voz = WebVoice(RecorderFake(), Transcriber(""), TTS())
    assert asyncio.run(voz.upload(_wav(audio=np.zeros(8000, dtype=np.float32)))) == {
        "text": "",
        "heard": False,
    }


def test_upload_nao_precisa_de_microfone_no_motor():
    """O mini headless transcreve: so o STT importa no caminho do navegador."""
    voz = WebVoice(None, Transcriber("oi"), TTS())
    assert voz.available()[0] is False
    assert asyncio.run(voz.upload(_wav()))["text"] == "oi"


def test_upload_sem_stt_levanta_unavailable():
    voz = WebVoice(None, None, TTS())
    with pytest.raises(VoiceUnavailable):
        asyncio.run(voz.upload(_wav()))


def test_upload_recusa_audio_longo_demais():
    from gaia.serve.voice import MAX_UPLOAD_SECONDS, SAMPLE_RATE

    longo = np.zeros(int((MAX_UPLOAD_SECONDS + 5) * SAMPLE_RATE), dtype=np.float32)
    voz = WebVoice(RecorderFake(), Transcriber("oi"), TTS())
    with pytest.raises(ValueError):
        asyncio.run(voz.upload(_wav(longo)))


# ------------------------------------------------------------- transporte ---


class MotorVoz:
    """Target minimo com as rotas de voz (o resto do protocolo nao e usado)."""

    def __init__(self, *, disponivel: bool = True, fala: bytes = b"abc") -> None:
        self.disponivel = disponivel
        self.fala = fala
        self.escutas: list[float] = []
        self.uploads: list[int] = []
        self.paradas = 0
        self.falas: list[str] = []

    def voice_status(self) -> dict[str, Any]:
        return {
            "available": self.disponivel,
            "reason": "" if self.disponivel else "sem mic",
            "tts": True,
            "source": "browser",
            "microphone": True,
        }

    def listen(self, window: float = 15.0) -> dict[str, Any]:
        self.escutas.append(window)
        return {"text": "que horas sao", "heard": True}

    def upload_voice(self, wav_bytes: bytes) -> dict[str, Any]:
        self.uploads.append(len(wav_bytes))
        return {"text": "que horas sao", "heard": True}

    def stop_listening(self) -> None:
        self.paradas += 1

    def speak(self, text: str) -> dict[str, Any]:
        self.falas.append(text)
        return {"audio": base64.b64encode(self.fala).decode(), "mime": "audio/mpeg"}

    def health(self) -> dict[str, Any]:
        return {"model": "x"}

    def sessions(self) -> list[dict[str, Any]]:
        return []

    def models(self) -> list[dict[str, Any]]:
        return []

    def switch_model(self, slug: str) -> list[str]:
        return []

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    def turn(self, text: str) -> str:
        return "r_1"

    def decide(self, request_id: str, decision: str) -> None:
        return None

    def abort(self) -> None:
        return None


class SemVoz(MotorVoz):
    voice_status = None  # type: ignore[assignment]
    listen = None  # type: ignore[assignment]
    stop_listening = None  # type: ignore[assignment]
    speak = None  # type: ignore[assignment]
    upload_voice = None  # type: ignore[assignment]


def _pedir(base: str, metodo: str, caminho: str, corpo: Any = None, tipo: str = "application/json"):
    import http.client
    import json

    host, porta = base.removeprefix("http://").split(":")
    conexao = http.client.HTTPConnection(host, int(porta), timeout=20)
    if corpo is None:
        dados = None
    elif isinstance(corpo, bytes):
        dados = corpo
    else:
        dados = json.dumps(corpo).encode()
    cabecalhos = {"Content-Type": tipo} if dados else {}
    conexao.request(metodo, caminho, body=dados, headers=cabecalhos)
    resposta = conexao.getresponse()
    bruto = resposta.read()
    conexao.close()
    return resposta.status, json.loads(bruto)


def _servidor(alvo: Any):
    srv = ServeServer(alvo, RemoteUI(), host="127.0.0.1", port=0)
    return srv, srv.start()


def test_voice_status_e_publico_para_o_chat():
    alvo = MotorVoz()
    srv, base = _servidor(alvo)
    try:
        status, corpo = _pedir(base, "GET", "/voice")
        assert status == 200
        assert corpo["available"] is True and corpo["tts"] is True
    finally:
        srv.shutdown()


def test_voice_escutar_devolve_transcricao():
    alvo = MotorVoz()
    srv, base = _servidor(alvo)
    try:
        status, corpo = _pedir(base, "POST", "/voice/listen", {"window": 12})
        assert status == 200
        assert corpo["text"] == "que horas sao" and corpo["heard"] is True
        assert corpo["window"] == 12.0
        assert alvo.escutas == [12.0]
    finally:
        srv.shutdown()


def test_voice_escutar_normaliza_janela_absurda():
    alvo = MotorVoz()
    srv, base = _servidor(alvo)
    try:
        status, corpo = _pedir(base, "POST", "/voice/listen", {"window": 9999})
        assert status == 200 and corpo["window"] == MAX_WINDOW_SECONDS
    finally:
        srv.shutdown()


def test_voice_speak_devolve_base64_do_tts():
    alvo = MotorVoz(fala=b"\x00mp3")
    srv, base = _servidor(alvo)
    try:
        status, corpo = _pedir(base, "POST", "/voice/speak", {"text": "oi"})
        assert status == 200
        assert base64.b64decode(corpo["audio"]) == b"\x00mp3"
        assert corpo["mime"] == "audio/mpeg"
        assert alvo.falas == ["oi"]
    finally:
        srv.shutdown()


def test_voice_speak_sem_texto_e_400():
    srv, base = _servidor(MotorVoz())
    try:
        assert _pedir(base, "POST", "/voice/speak", {"text": "  "})[0] == 400
    finally:
        srv.shutdown()


def test_voice_upload_recebe_wav_e_transcreve():
    alvo = MotorVoz()
    srv, base = _servidor(alvo)
    try:
        wav = _wav()
        status, corpo = _pedir(
            base, "POST", "/voice/upload", wav, tipo="audio/wav"
        )
        assert status == 200
        assert corpo == {
            "ok": True,
            "text": "que horas sao",
            "heard": True,
            "window": 0,
        }
        assert alvo.uploads == [len(wav)]  # o body chegou inteiro
    finally:
        srv.shutdown()


def test_voice_upload_sem_bytes_e_400():
    srv, base = _servidor(MotorVoz())
    try:
        status, corpo = _pedir(base, "POST", "/voice/upload", b"", tipo="audio/wav")
        assert status == 400 and corpo["error"] == "empty_audio"
    finally:
        srv.shutdown()


def test_voice_upload_invalido_e_400_nao_500():
    class Recusa(MotorVoz):
        def upload_voice(self, wav_bytes: bytes) -> dict[str, Any]:
            from gaia.serve.voice import decode_wav

            decode_wav(wav_bytes)
            return {"text": "", "heard": False}

    srv, base = _servidor(Recusa())
    try:
        status, corpo = _pedir(base, "POST", "/voice/upload", b"nao sou wav", tipo="audio/wav")
        assert status == 400 and corpo["error"] == "invalid_audio"
    finally:
        srv.shutdown()


def test_voice_upload_sem_metodo_no_motor_e_503():
    srv, base = _servidor(SemVoz())
    try:
        status, corpo = _pedir(base, "POST", "/voice/upload", _wav(), tipo="audio/wav")
        assert status == 503 and corpo["error"] == "voice_unavailable"
    finally:
        srv.shutdown()


def test_voice_upload_acima_do_limite_e_413():
    from gaia.serve.server import UPLOAD_MAX_BYTES

    srv, base = _servidor(MotorVoz())
    try:
        grande = b"RIFF" + b"\x00" * (UPLOAD_MAX_BYTES + 1)
        status, corpo = _pedir(base, "POST", "/voice/upload", grande, tipo="audio/wav")
        assert status == 413 and corpo["error"] == "audio_too_large"
    finally:
        srv.shutdown()


def test_voice_status_publica_a_fonte_do_audio():
    srv, base = _servidor(MotorVoz())
    try:
        status, corpo = _pedir(base, "GET", "/voice")
        assert status == 200
        assert corpo["source"] == "browser"
        assert corpo["microphone"] is True
    finally:
        srv.shutdown()


def test_voice_stop_e_idempotente():
    alvo = MotorVoz()
    srv, base = _servidor(alvo)
    try:
        for _ in range(2):
            status, corpo = _pedir(base, "POST", "/voice/stop", {})
            assert status == 200 and corpo["ok"] is True
        assert alvo.paradas == 2
    finally:
        srv.shutdown()


def test_motor_sem_voz_responde_503_sem_estourar():
    srv, base = _servidor(SemVoz())
    try:
        status, corpo = _pedir(base, "GET", "/voice")
        assert status == 200
        assert corpo["available"] is False and corpo["reason"]

        assert _pedir(base, "POST", "/voice/listen", {})[0] == 503
        assert _pedir(base, "POST", "/voice/speak", {"text": "oi"})[0] == 503
        # O stop nao depende do metodo existir.
        assert _pedir(base, "POST", "/voice/stop", {})[0] == 200
    finally:
        srv.shutdown()


def test_escuta_indisponivel_vira_503(monkeypatch):
    class SemMic(MotorVoz):
        def listen(self, window: float = 15.0) -> dict[str, Any]:
            raise VoiceUnavailable("ja existe uma escuta em andamento")

    srv, base = _servidor(SemMic())
    try:
        status, corpo = _pedir(base, "POST", "/voice/listen", {})
        assert status == 503
        assert corpo["error"] == "voice_unavailable"
    finally:
        srv.shutdown()


# ------------------------------------------------- motor de verdade (ponte) ---


class _Orch:
    """Orchestrator falso com recorder/transcriber/tts, como o `_build_app` monta."""

    def __init__(self) -> None:
        self.recorder = RecorderFake(audio=_audio())
        self.transcriber = Transcriber("duas da tarde")
        self.tts = TTS(dados=b"ID3ok")
        self._stt_lock = asyncio.Lock()
        self.cfg = type("Cfg", (), {"agent": type("A", (), {"model_slug": lambda self: "x"})()})()
        self.session_id = "s1"
        self.mode_name = "assistente"

    async def answer_once(self, text: str, **_: Any) -> str:
        return f"eco: {text}"


def _loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def test_target_traduz_listen_e_speak_para_o_motor():
    loop, thread = _loop()
    try:
        orch = _Orch()
        alvo = OrchestratorTarget(orch, RemoteUI())
        alvo.attach_loop(loop)

        assert alvo.voice_status()["available"] in (True, False)  # depende do host
        escuta = alvo.listen(7)
        assert escuta == {"text": "duas da tarde", "heard": True}
        assert orch.recorder.janelas == [7.0]

        fala = alvo.speak("oi")
        assert base64.b64decode(fala["audio"]) == b"ID3ok"
        assert fala["mime"] == "audio/mpeg"

        alvo.stop_listening()
        assert orch.recorder.paradas == 1
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


def test_target_sem_recorder_fica_indisponivel():
    class SemAudio(_Orch):
        def __init__(self) -> None:
            super().__init__()
            self.recorder = None  # motor sem microfone
            self.cfg.serve = type("S", (), {"voice_source": "server"})()

    alvo = OrchestratorTarget(SemAudio(), RemoteUI())
    dados = alvo.voice_status()
    assert dados["available"] is False and dados["reason"]
    assert dados["source"] == "server"


def test_target_sem_microfone_ainda_aceita_upload_do_navegador():
    """O ponto do modo novo: mini headless (sem mic) transcreve o WAV do Mac."""

    class SemAudio(_Orch):
        def __init__(self) -> None:
            super().__init__()
            self.recorder = None

    alvo = OrchestratorTarget(SemAudio(), RemoteUI())
    dados = alvo.voice_status()
    assert dados["available"] is True
    assert dados["source"] == "browser"
    assert dados["microphone"] is False


def test_target_traduz_upload_para_o_motor():
    loop, thread = _loop()
    try:
        orch = _Orch()
        alvo = OrchestratorTarget(orch, RemoteUI())
        alvo.attach_loop(loop)

        dados = alvo.upload_voice(_wav())
        assert dados == {"text": "duas da tarde", "heard": True}
        assert orch.recorder.janelas == []  # nao tocou no microfone do motor
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)


# ------------------------------------------------- fase da escuta (`GET /voice`)


def test_voz_publica_a_fase_da_escuta():
    """A tela sonda `GET /voice` para trocar de "ouvindo" para "transcrevendo".

    Sem isso o botao fica em "ouvindo" ate o POST /voice/listen terminar (o
    Whisper inteiro) e parece que a tecla nao fez nada.
    """
    from gaia.serve import voice as modulo

    class Lento(RecorderFake):
        def record_until_silence(self, wait_timeout: float | None = None) -> Any:
            fases.append(voz.state)
            time.sleep(0.1)  # janela em curso: e aqui que a tela sonda
            return super().record_until_silence(wait_timeout)

    class TranscriberLento(Transcriber):
        def transcribe(self, audio: Any) -> str:
            fases.append(voz.state)
            return super().transcribe(audio)

    fases: list[str] = []
    voz = WebVoice(Lento(audio=_audio()), TranscriberLento("oi"), TTS())

    async def corrida() -> dict[str, Any]:
        tarefa = asyncio.create_task(voz.listen(5))
        await asyncio.sleep(0.05)
        durante = voz.state
        dados = await tarefa
        return {"inicio": durante, "fim": voz.state, "dados": dados}

    saida = asyncio.run(corrida())
    assert fases == [modulo.STATE_LISTENING, modulo.STATE_TRANSCRIBING]
    assert saida["inicio"] == modulo.STATE_LISTENING
    assert saida["fim"] == modulo.STATE_IDLE
    assert saida["dados"]["text"] == "oi"


def test_voice_status_traz_a_fase(monkeypatch):
    monkeypatch.setattr(
        "gaia.audio.recorder.Recorder.available", staticmethod(lambda: (True, "ok"))
    )
    alvo = OrchestratorTarget(_Orch(), RemoteUI())
    assert alvo.voice_status()["state"] == "idle"

    srv, base = _servidor(alvo)
    try:
        status, corpo = _pedir(base, "GET", "/voice")
        assert status == 200 and corpo["state"] == "idle"
    finally:
        srv.shutdown()
