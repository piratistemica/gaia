# Modo voz com microfone do navegador (WAV) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** O navegador grava áudio com o microfone local, envia WAV para o mini e o mini transcreve com Whisper; o painel permite escolher entre microfone do navegador e do servidor.

**Architecture:** Adicionar rota `POST /voice/upload` no servidor que decodifica WAV para `np.ndarray` e transcreve com o `WebVoice` existente. No front, gravar WAV com `AudioWorklet`/`ScriptProcessor` e enviar. No Control Center, adicionar seletor de fonte de áudio persistido no TOML.

**Tech Stack:** Python 3.12, numpy, faster-whisper, JavaScript Web Audio API.

---

### Task 1: Decodificador WAV no servidor

**Files:**
- Modify: `gaia/serve/voice.py`
- Test: `tests/test_serve_voice.py`

**Step 1: Write the failing test**

```python
import numpy as np

def test_decode_wav_mono_16k():
    from gaia.serve.voice import decode_wav
    # Cria um WAV 16-bit 16kHz mono de 1s com senoide 440Hz
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    # Header WAV mínimo
    import struct
    data = samples.tobytes()
    chunk_size = 36 + len(data)
    header = b"RIFF"
    header += struct.pack("<I", chunk_size)
    header += b"WAVE"
    header += b"fmt "
    header += struct.pack("<I", 16)  # subchunk size
    header += struct.pack("<H", 1)   # PCM
    header += struct.pack("<H", 1)   # channels
    header += struct.pack("<I", sr)
    header += struct.pack("<I", sr * 2)
    header += struct.pack("<H", 2)   # block align
    header += struct.pack("<H", 16)  # bits per sample
    header += b"data"
    header += struct.pack("<I", len(data))
    wav_bytes = header + data

    audio = decode_wav(wav_bytes)
    assert audio.dtype == np.float32
    assert len(audio) == sr
    assert -1.0 <= audio.max() <= 1.0
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_serve_voice.py::test_decode_wav_mono_16k -v
```

Expected: FAIL — `decode_wav` not defined.

**Step 3: Write minimal implementation**

```python
def decode_wav(data: bytes) -> np.ndarray:
    """Decodifica um WAV mono/estéreo 16-bit para float32 mono @16kHz."""
    import struct
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("arquivo nao e WAV")
    # fmt chunk
    fmt_pos = data.find(b"fmt ")
    channels = struct.unpack("<H", data[fmt_pos + 10:fmt_pos + 12])[0]
    sample_rate = struct.unpack("<I", data[fmt_pos + 12:fmt_pos + 16])[0]
    bits_per_sample = struct.unpack("<H", data[fmt_pos + 20:fmt_pos + 22])[0]
    if bits_per_sample != 16:
        raise ValueError("so WAV 16-bit e suportado")
    # data chunk
    data_pos = data.find(b"data")
    data_size = struct.unpack("<I", data[data_pos + 4:data_pos + 8])[0]
    raw = np.frombuffer(data[data_pos + 8:data_pos + 8 + data_size], dtype=np.int16)
    if channels == 2:
        raw = raw.reshape(-1, 2).mean(axis=1).astype(np.int16)
    audio = raw.astype(np.float32) / 32768.0
    if sample_rate != 16000:
        # simples downsampling/upsampling por interpolação linear
        n = int(len(audio) * 16000 / sample_rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return audio.astype(np.float32)
```

**Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_serve_voice.py::test_decode_wav_mono_16k -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add gaia/serve/voice.py tests/test_serve_voice.py
git commit -m "feat(voice): decodifica WAV 16-bit para np.ndarray"
```

---

### Task 2: Método `upload_voice` no `WebVoice`

**Files:**
- Modify: `gaia/serve/voice.py`
- Test: `tests/test_serve_voice.py`

**Step 1: Write the failing test**

```python
def test_upload_voice_transcreve_wav():
    from gaia.serve.voice import WebVoice
    import struct
    sr = 16000
    t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    data = samples.tobytes()
    chunk_size = 36 + len(data)
    header = b"RIFF" + struct.pack("<I", chunk_size) + b"WAVE"
    header += b"fmt " + struct.pack("<I", 16) + struct.pack("<H", 1) + struct.pack("<H", 1)
    header += struct.pack("<I", sr) + struct.pack("<I", sr * 2) + struct.pack("<H", 2) + struct.pack("<H", 16)
    header += b"data" + struct.pack("<I", len(data))
    wav = header + data

    voz = WebVoice(RecorderFake(audio=_audio()), Transcriber("ola"), TTS())
    dados = asyncio.run(voz.upload(wav))
    assert dados["heard"] is True
    assert dados["text"] == "ola"
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_serve_voice.py::test_upload_voice_transcreve_wav -v
```

Expected: FAIL — `upload` not defined.

**Step 3: Write minimal implementation**

```python
async def upload(self, wav_bytes: bytes) -> dict[str, Any]:
    """Decodifica um WAV e transcreve com o Whisper local."""
    ok, motivo = self.available()
    if not ok:
        raise VoiceUnavailable(motivo)
    audio = decode_wav(wav_bytes)
    if len(audio) == 0:
        return {"text": "", "heard": False}
    self.state = STATE_TRANSCRIBING
    try:
        texto = await asyncio.to_thread(self._transcribe, audio)
        return {"text": texto, "heard": bool(texto)}
    finally:
        self.state = STATE_IDLE
```

**Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_serve_voice.py::test_upload_voice_transcreve_wav -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add gaia/serve/voice.py tests/test_serve_voice.py
git commit -m "feat(voice): upload de WAV e transcrição"
```

---

### Task 3: Rota `POST /voice/upload` no servidor

**Files:**
- Modify: `gaia/serve/server.py`
- Modify: `gaia/serve/app.py`
- Test: `tests/test_serve_voice.py`

**Step 1: Write the failing test**

```python
def test_voice_upload_recebe_wav_e_transcreve():
    alvo = MotorVoz()
    srv, base = _servidor(alvo)
    try:
        # Monta um WAV válido
        import struct
        sr = 16000
        t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
        samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
        data = samples.tobytes()
        header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        header += b"fmt " + struct.pack("<I", 16) + struct.pack("<H", 1) + struct.pack("<H", 1)
        header += struct.pack("<I", sr) + struct.pack("<I", sr * 2) + struct.pack("<H", 2) + struct.pack("<H", 16)
        header += b"data" + struct.pack("<I", len(data))
        wav = header + data
        status, corpo = _pedir(base, "POST", "/voice/upload", body=wav)
        assert status == 200
        assert corpo["text"] == "que horas sao"
    finally:
        srv.shutdown()
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_serve_voice.py::test_voice_upload_recebe_wav_e_transcreve -v
```

Expected: FAIL — 404 ou rota não existe.

**Step 3: Write minimal implementation**

`gaia/serve/server.py`:
```python
if rota == "/voice/upload":
    self._upload_voz(corpo)
    return
```

```python
def _upload_voz(self, corpo: bytes) -> None:
    funcao = _opcional(self.app.target, "upload_voice")
    if funcao is None:
        self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "voice_unavailable", "modo voz indisponivel")
        return
    if not isinstance(corpo, bytes) or len(corpo) == 0:
        self._erro(HTTPStatus.BAD_REQUEST, "empty_audio", "audio vazio")
        return
    try:
        dados = funcao(corpo)
    except VoiceUnavailable as exc:
        self._erro(HTTPStatus.SERVICE_UNAVAILABLE, "voice_unavailable", str(exc))
        return
    except Exception as exc:
        logger.warning("upload de voz falhou: %s", exc)
        self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, "voice_failed", str(exc))
        return
    self._json(HTTPStatus.OK, {"ok": True, "text": str(dados.get("text") or ""), "heard": bool(dados.get("heard")), "window": 0})
```

`gaia/serve/app.py`:
```python
def upload_voice(self, wav_bytes: bytes) -> dict[str, Any]:
    if self._loop is None:
        raise RuntimeError("serve sem loop asyncio ligado")
    futuro = asyncio.run_coroutine_threadsafe(self.voz.upload(wav_bytes), self._loop)
    return futuro.result(timeout=60.0)
```

**Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_serve_voice.py::test_voice_upload_recebe_wav_e_transcreve -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add gaia/serve/server.py gaia/serve/app.py tests/test_serve_voice.py
git commit -m "feat(serve): rota POST /voice/upload para áudio do navegador"
```

---

### Task 4: Gravação WAV no navegador

**Files:**
- Modify: `gaia/serve/static/index.html`
- Test: `tests/test_serve_web.py`

**Step 1: Write the failing test**

```python
def test_modo_voz_grava_wav_no_navegador():
    texto, _ = _html()
    assert "navigator.mediaDevices.getUserMedia" in texto
    assert "audio/wav" in texto
    assert "/voice/upload" in texto
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_serve_web.py::test_modo_voz_grava_wav_no_navegador -v
```

Expected: FAIL

**Step 3: Write minimal implementation**

Adicionar no `index.html`:
- `vozGravar()` que chama `navigator.mediaDevices.getUserMedia({ audio: true })`.
- Cria `AudioContext`, `ScriptProcessorNode`, grava buffer de 16-bit, monta WAV.
- `vozPararGravacao()` que finaliza e faz `POST /voice/upload`.
- Ajustar `vozCiclo()` para, quando `voice_source === "browser"`, gravar no navegador ao invés de chamar `/voice/listen`.

**Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_serve_web.py::test_modo_voz_grava_wav_no_navegador -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add gaia/serve/static/index.html tests/test_serve_web.py
git commit -m "feat(web): gravação WAV no navegador e upload"
```

---

### Task 5: Seletor de fonte de áudio no Control Center

**Files:**
- Modify: `gaia/serve/control.html`
- Modify: `gaia/serve/static/control.js`
- Modify: `gaia/serve/control_api.py`
- Modify: `gaia/config.py`
- Test: `tests/test_control_api.py`

**Step 1: Write the failing test**

```python
def test_config_fonte_de_audio():
    # config.voice_source default = "browser"
    from gaia.config import Config
    cfg = Config.load()
    assert cfg.serve.voice_source in ("browser", "server")
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_config.py::test_config_fonte_de_audio -v
```

Expected: FAIL — `voice_source` not defined.

**Step 3: Write minimal implementation**

`gaia/config.py`:
```python
class ServeConfig(BaseModel):
    ...
    voice_source: str = "browser"  # "browser" | "server"
```

`gaia/serve/control_api.py`: adicionar rota `GET /control/api/voice-source` e `POST /control/api/voice-source`.

`gaia/serve/control.html` + `control.js`: seletor de fonte.

**Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_config.py::test_config_fonte_de_audio tests/test_control_api.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add gaia/config.py gaia/serve/control_api.py gaia/serve/control.html gaia/serve/static/control.js tests/test_control_api.py
git commit -m "feat(control): seletor de fonte de áudio para modo voz"
```

---

### Task 6: Integração e testes de fumaça

**Files:**
- All above

**Step 1: Rodar todos os testes de voz e web**

```bash
.venv/bin/python -m pytest tests/test_serve_web.py tests/test_serve_voice.py tests/test_control_api.py -q
```

Expected: PASS (ou falhas preexistentes).

**Step 2: Verificar lint e mypy**

```bash
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

Expected: All clear.

**Step 3: Commit final**

```bash
git commit -am "feat(voice): modo voz usa microfone do navegador via WAV upload"
```

---

### Task 7: Deploy e validação

**Files:**
- N/A

**Step 1: Deploy para o mini**

```bash
scripts/gmin.sh deploy
```

**Step 2: Abrir a web no Mac e testar o modo voz**

- Clicar no botão 🎙 voz.
- Permitir o microfone.
- Falar e verificar se a transcrição chega.
- Testar a tecla `M` para calar/religar.
- No Control Center, trocar para "microfone do servidor" e testar.

**Step 3: Commit do design doc**

```bash
git add docs/plans/2026-11-23-voz-navegador-wav-design.md docs/plans/2026-11-23-voz-navegador-wav.md
git commit -m "docs(voice): plano de implementação do modo voz com microfone do navegador"
```

---

## Resumo dos commits esperados

1. `feat(voice): decodifica WAV 16-bit para np.ndarray`
2. `feat(voice): upload de WAV e transcrição`
3. `feat(serve): rota POST /voice/upload para áudio do navegador`
4. `feat(web): gravação WAV no navegador e upload`
5. `feat(control): seletor de fonte de áudio para modo voz`
6. `feat(voice): modo voz usa microfone do navegador via WAV upload`
7. `docs(voice): plano de implementação do modo voz com microfone do navegador`
