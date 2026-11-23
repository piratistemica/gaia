# Handoff — Modo voz com microfone do navegador

> **Data:** 2026-11-23
> **Status:** design + plano prontos; implementação pendente

## PROMPT para a próxima sessão

Continue a implementação do modo voz do web com microfone do navegador. A base
está no repositório `~/gaia`.

### Leitura obrigatória

1. `docs/plans/2026-11-23-voz-navegador-wav-design.md` — design aprovado.
2. `docs/plans/2026-11-23-voz-navegador-wav.md` — plano de implementação
   task-by-task.
3. Este arquivo (`docs/plans/2026-11-23-handoff-voz-navegador.md`).

### Estado atual

- A correção de UX (Enter envia, M mudo, botão mostra "transcrevendo…") já
  foi aplicada e o deploy parcial (código no mini) foi feito, mas **ainda não
  foi commitado**. Os arquivos modificados são:
  - `gaia/serve/static/index.html`
  - `gaia/serve/app.py`
  - `gaia/serve/server.py`
  - `gaia/serve/voice.py`
  - `tests/test_serve_web.py`
  - `tests/test_serve_voice.py`
  - `README.md`
- O novo design de voz pelo navegador **ainda não foi implementado**.

### Tarefa principal

Implementar o modo voz com microfone do navegador seguindo o plano
`docs/plans/2026-11-23-voz-navegador-wav.md`.

Resumo da arquitetura:

1. Navegador grava WAV 16-bit mono 16kHz via Web Audio API.
2. Envia via `POST /voice/upload`.
3. Servidor decodifica WAV para `np.ndarray` com numpy e transcreve com
   `faster-whisper` local.
4. O resto do fluxo (`/turn`, `/voice/speak`) continua igual.
5. Control Center terá seletor de fonte de áudio: "navegador" ou "servidor".

### Pontos de atenção

- **Não reimplementar a roda:** reutilize `WebVoice`, `RecorderFake` e as
  rotas existentes de `/voice` sempre que possível.
- **Backward compatibility:** `POST /voice/listen` e `POST /voice/stop`
  continuam existindo para o modo "microfone do servidor".
- **Testes:** o plano especifica testes unitários para decodificação WAV,
  upload e rotas. Mantenha o padrão TDD do projeto.
- **Deploy:** ao final, faça deploy com `scripts/gmin.sh deploy` e valide no
  Mac/mini.

### Verificações obrigatórias antes de finalizar

- [ ] `pytest` passando.
- [ ] `ruff check gaia tests` sem erros.
- [ ] `mypy` sem erros.
- [ ] Deploy realizado no mini via `gmin deploy`.
- [ ] Teste manual no Mac confirmando que o microfone do navegador funciona.

### Dúvidas? Conferir

- `AGENTS.md` para regras gerais do repo.
- `docs/plans/2026-11-20-handoff.md` para estado operacional do mini/Mac.
- Arquivos de teste `tests/test_serve_voice.py` e `tests/test_serve_web.py`
  para padrões de teste.
