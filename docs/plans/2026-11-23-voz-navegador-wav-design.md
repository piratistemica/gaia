# Design: modo voz no web com microfone do navegador

**Data:** 2026-11-23
**Status:** design aprovado

## Contexto

Hoje o modo voz do web usa o microfone do *servidor* (`gaia serve`):

- Navegador pede `POST /voice/listen`.
- O mini grava com `sounddevice` e transcreve com Whisper local.
- O navegador só desenha o estado e toca a resposta.

O objetivo é inverter: o **navegador** (Mac ou outra máquina) capta o áudio e
envia para o mini transcrever. Isso resolve o problema de o mini não ter
microfone útil ou estar headless.

## Decisões de arquitetura

### 1. Formato: WAV 16-bit mono 16kHz

O navegador grava em WAV via `ScriptProcessorNode`/`AudioWorklet` e faz upload
dos bytes. O servidor decodifica para `np.ndarray` float32 @ 16kHz mono e manda
para o Whisper.

**Por que não Opus/webm:**

- Evita adicionar `ffmpeg`, `librosa` ou `soundfile` ao servidor.
- O projeto já depende de `numpy`; parse de WAV é trivial.
- Frases de voz curtas (~5-30 s) geram arquivos pequenos o suficiente.

### 2. Rota nova: `POST /voice/upload`

Aceita o áudio em bytes (Content-Type `audio/wav`).
Retorna o mesmo JSON de `POST /voice/listen`:

```json
{
  "ok": true,
  "text": "texto transcrito",
  "heard": true,
  "window": 0
}
```

A fase de transcrição será publicada no `WebVoice.state`, assim como na rota
`/voice/listen`, para a tela mostrar "transcrevendo…".

### 3. Seleção da fonte de áudio

No painel do Control Center (`/control`) será adicionada uma seção de
configuração de voz com um seletor:

- **Microfone do navegador** (padrão no web quando disponível).
- **Microfone do servidor** (modo antigo, `POST /voice/listen`).

A escolha é gravada no `MemoryStore`/config TOML e aplicada no momento da
escuta. Se o navegador escolher "microfone do servidor", o fluxo antigo
continua. Se escolher "microfone do navegador", o fluxo novo é usado.

### 4. Fluxo do modo voz com microfone do navegador

1. Usuário clica no botão 🎙 voz.
2. A tela verifica permissão do microfone via `navigator.mediaDevices.getUserMedia`.
3. Enquanto o botão estiver no estado "ouvindo", o navegador grava em buffer.
4. Quando o usuário aperta `Enter` ou detecta silêncio, o navegador finaliza o WAV.
5. Envia `POST /voice/upload` com o WAV.
6. O servidor transcreve e devolve o texto.
7. O resto do fluxo (`/turn`, `/voice/speak`) continua igual.

### 5. Backward compatibility

- `POST /voice/listen` continua existindo.
- `POST /voice/stop` continua existindo para o modo servidor.
- A tela escolhe o caminho correto com base na configuração.

### 6. Segurança

- Rota autenticada com o mesmo token do chat.
- Limite de tamanho do body (ex: 5 MB) para evitar abuso.
- O servidor não guarda o áudio em disco; processa em memória e descarta.

## Arquivos afetados

- `gaia/serve/static/index.html` — gravação WAV, envio, seleção de fonte.
- `gaia/serve/voice.py` — decodificação WAV e transcrição.
- `gaia/serve/app.py` — método `upload_voice`.
- `gaia/serve/server.py` — rota `POST /voice/upload`.
- `gaia/serve/control.html` / `control.js` — seletor de fonte de áudio.
- `gaia/config.py` — novo campo `serve.voice_source` (ou similar).
- `tests/test_serve_voice.py` — testes da nova rota.
- `tests/test_serve_web.py` — testes do novo JS.

## Testes de aceitação

1. Navegador grava e envia WAV; servidor transcreve e responde.
2. Microfone do servidor continua funcionando quando selecionado.
3. Botão mostra "transcrevendo…" durante a transcrição do upload.
4. Tecla `M` cala/religa o microfone do navegador.
5. Painel permite trocar a fonte de áudio e a escolha persiste.
