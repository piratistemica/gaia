# Plano — Mac como cliente fino (e o que falta para isso valer)

> **Status: diagnóstico verificado em 22/11/2026 02:40, nada aplicado.**
> Referência atemporal: `../../AGENTS.md`. Plano anterior e relacionado:
> [`2026-11-19-serve-cliente-fino.md`](2026-11-19-serve-cliente-fino.md).

**Pergunta que originou este documento:** "o que tem que mudar na estrutura pro
Mac virar cliente fino? eu pensei que já era".

**Resposta curta:** o *motor* pode ser remoto desde 20/11; a *TUI* não. O
`gaia serve` + `gaia --remote` entregues na fase 1 são o caminho Rich
unidirecional (tela → mini). O plano original dizia, com todas as letras,
**"Fora de escopo na fase 1: Textual remota (primeiro Rich, depois Textual)"**
(`2026-11-19-serve-cliente-fino.md:214`) — e a parte Textual nunca foi feita.
Enquanto isso, `gaia` sem argumentos no Mac abre o motor **local**, escreve no
`gaia.db` e no SecondBrain da máquina, e é isso que volta a divergir.

Este documento lista o que existe, o que falta, o dano real de cada lacuna e o
custo de fechar cada uma. A ordem recomendada está na §6.

---

## 0. O dano que motivou a conversa (é maior do que "conflito de prosa")

O `scripts/sb-sync.sh` protege contra **conflito de merge** (aborta com exit 2).
Ele não protege contra **sobrescrita silenciosa**, que é o caso comum:

1. mini consolida 21/11 às 23:00 → `Memoria/Memória 2026-11-21.md` → commit → push.
2. Mac sincroniza e recebe o arquivo.
3. Mac abre a Gaia (TUI). `Orchestrator._bootstrap()` chama
   `_start_background_workers()` (`gaia/controller/orchestrator.py:213`), que
   dispara `MemoryMixin._start_memory_worker()` (`orchestrator.py:228`).
4. `consolidate_day` encontra a nota com `brain.resolve()`
   (`gaia/memory/consolidation.py:247`) e chama `update_note`
   (`consolidation.py:249`): **sobrescreve** o texto do mini com o texto do Mac.
5. O script commita a mudança do Mac e empurra. Git não vê conflito.
   **Exit 0, nenhum aviso, o texto do mini foi embora.**

O mesmo vale para o sonho (`gaia/memory/dream.py:516-523`). As guardas
`memory_dreamed:<dia>` e `memory_consolidated:<dia>` vivem no `gaia.db` **de
cada máquina** (`gaia/memory/dream.py:289`,
`gaia/memory/consolidation.py:216`), então as duas máquinas sonham/consolidam o
mesmo dia e a segunda a escrever apaga a primeira. O `exit 2` só aparece quando
as duas mexem no arquivo *entre* dois syncs.

**Conclusão:** sincronizar texto é paliativo; a regra "um único escritor" é o
que fecha a classe inteira. O caminho é o Mac parar de escrever.

---

## 1. O que já existe (não precisa ser feito)

| Peça | Onde | Estado |
|---|---|---|
| Servidor HTTP + SSE, sem dependência nova | `gaia/serve/server.py` | entregue |
| Ponte motor ↔ transporte (`run_coroutine_threadsafe`) | `gaia/serve/app.py` (`OrchestratorTarget`) | entregue |
| `RemoteUI`: serializa 36 métodos de UI em eventos | `gaia/interface/remote.py:30` (`RENDER_METHODS`) | entregue |
| Cliente Rich: SSE + POST, reconexão com `Last-Event-ID` | `gaia/client/remote_client.py` | entregue |
| Aprovação remota (`permission` → `POST /permission/{id}`) | `app.py`, `server.py:231` | entregue |
| Cliente web (`GET /`) | `gaia/serve/static/` | entregue |
| Rotas `GET /health`, `/sessions`, `/models`, `/search`, `/events`, `POST /turn`, `/abort`, `/model` | `server.py` | entregue |
| Dono único do Telegram no mini (`gaia-serve.service`) | `deploy/` | cutover feito em 20/11 |

Ou seja: a fronteira de rede existe e é fina. `gaia --remote URL` no Mac **não
escreve banco nenhum** — o critério de aceite da fase 1 é atendido.

---

## 2. As seis lacunas

| # | Lacuna | Onde | Dano | Custo |
|---|---|---|---|---|
| 1 | `gaia` sem argumentos no Mac monta o motor local | `gaia/cli.py:53-54, 83` | o modo padrão é o que escreve; o thin é opt-in | baixo |
| 2 | `TextualApp` exige o `Orchestrator` como `voice` | `gaia/interface/textual_app.py:237` | a TUI (padrão `interface.ui = "textual"`, `config.py:198`) não sobe sobre o cliente remoto | **alto** |
| 3 | `ui.confirm()` não existe no `RemoteUI` | `gaia/controller/registration.py:46` vs `remote.py:30` | `confirm_create = true` no mini → criar nota remota quebra com `AttributeError` | baixo |
| 4 | Comando de barra remoto: 12 eventos emitidos não são desenhados | `remote_client.py` (`render`) | `/buscar`, `/nota`, `/lembretes` remotos não mostram nada | baixo |
| 5 | Voz não existe no cliente | `gaia/client/remote_client.py` | `gaia --remote` não fala nem ouve; o plano previa "voz no cliente" | médio |
| 6 | Nenhuma trava impede a escrita local | `gaia/secondbrain/notes.py` | um `gaia` sem `--remote` reabre a divergência da §0 | baixo |

### 2.1 `gaia` sem argumentos (lacuna 1)

`cli.py:53` só desvia para o cliente remoto quando `args.remote` **e**
`args.command is None`. Não há config equivalente a `serve.host` do lado do
cliente: para o Mac ser thin por padrão, falta uma decisão explícita de
"este Mac é cliente de tal motor". A alternativa óbvia — um campo em
`[serve]` — mistura papel de servidor com papel de cliente; o lugar natural é
uma seção `[client]` (`remote_url`, `token`, `voice_local`).

### 2.2 `TextualApp` sobre o remoto (lacuna 2, o grosso do trabalho)

A TUI não fala com o `Orchestrator` por eventos, fala por **atributos e
métodos diretos**. Levantamento do que ela usa (`textual_app.py`):

`cfg`, `client`, `tts`, `transcriber`, `agenda`, `focus`, `goals`, `ui`,
`session_id`, `mode_key`, `mode_name`, `modes()`, `history()`, `sessions()`,
`new_session()`, `select_conversation()`, `select_mode()`,
`model_messages()`, `show_model_options()`, `mark_session_dirty()`,
`speak_text()`, `translate_zh()`.

Todos existem no `Orchestrator` (conferido: `orchestrator.py:661-708`,
`commands.py:264,329,740`, `contract.py:318`). Nenhum existe como rota do
`serve`. Fechar a lacuna é escrever um **adaptador cliente-side** com essa
mesma superfície, alimentado por rotas novas, sem tocar no `Orchestrator` nem
no `RemoteUI`.

Rotas que faltariam:

| Rota | Serve para | Mapeia em |
|---|---|---|
| `GET /history` | histórico do turno | `Orchestrator.history()` |
| `GET /modes` / `POST /mode` | barra de modo (Tab) | `modes()`, `select_mode()` |
| `GET /focus`, `/goals` | painel (F5) | `focus`, `goals` |
| `POST /session` | nova sessão (F2) | `new_session()`, `select_conversation()` |
| `POST /model/preview` | escolher modelo (F6) | `show_model_options()`, `model_messages()` |
| `POST /confirm/{id}` | ver lacuna 3 | `ui.confirm()` |
| evento `command_output` | ver lacuna 4 | `_handle_command()` |

Risco de escopo: é assim que a TUI remota vira "uma segunda TUI" mantida em
paralelo. Mitigação: o adaptador só faz *pass-through* — nenhuma regra de
negócio nova no cliente.

### 2.3 `ui.confirm` (lacuna 3)

`TerminalUI.confirm()` existe (`gaia/interface/terminal.py:539`);
`RemoteUI` **não** define `confirm` (verificado: `hasattr(RemoteUI, "confirm")
== False`). Funciona hoje só porque `secondbrain.confirm_create` é `false` por
padrão. No dia em que for `true`, `RegistrationMixin._handle_register`
(`registration.py:46`) levanta `AttributeError` num `asyncio.to_thread` — o
erro não é capturado e derruba o turno. É bug latente, independente de o Mac
virar thin.

Correção mínima: `confirm` entra em `RENDER_METHODS`, vira evento
`confirm`/`confirm_result` e `POST /confirm/{id}`, com o mesmo desenho de
`permission` (§ `remote.py:256-302`). Alternativa mais barata (mas pior a
longo prazo): `RemoteUI.confirm` devolve o `default`.

### 2.4 Comando de barra remoto (lacuna 4)

`OrchestratorTarget._executar` (`gaia/serve/app.py:142-149`) emite
`run_finished(text=resposta)`. Quando o texto começa com `/`,
`Orchestrator.answer_once` (`orchestrator.py:336-339`) executa
`_handle_command` e retorna `""` — o comando *funciona* no mini, mas o efeito
sai pelos métodos de `RemoteUI` (`search_results`, `print_note`, `reminders`…),
e o `RemoteClient.render` descarta esses eventos sem avisar.

A lacuna 4 é maior do que parece. Comparando o que o `RemoteUI` emite com o que
o cliente Rich trata (levantamento por AST de `remote.py` e `remote_client.py`):

```
RemoteUI emite (28): check closed context_hits erase_input help note
   note_preview notice partial permission permission_ignored
   permission_timeout proactive prompt reasoning reminders say search_results
   session status stream_begin stream_delta stream_end tokens tool transcript
   user watchlist
app.py emite (2):     run_finished aborted
cliente trata (17):   check closed notice permission permission_timeout
   proactive reasoning run_finished say session status stream_begin
   stream_delta stream_end tokens tool user
```

**Faltam 12 tipos** (e `aborted`, que o `app.py` emite e ninguém desenha) — e
dois deles derrubam comandos de barra inteiros:

| Evento | Comando afetado |
|---|---|
| `search_results` | `/buscar` remoto não mostra nada |
| `note` | `/nota` remoto não mostra nada |
| `reminders` | `/lembretes` remoto não mostra nada |
| `watchlist`, `context_hits`, `help`, `note_preview`, `transcript`, `partial`, `erase_input`, `prompt`, `permission_ignored`, `aborted` | efeitos visuais e de estado |

Corrigir isso é o que faz `/buscar`, `/nota` e `/lembretes` funcionarem sem
rotear comando por comando: o evento já viaja, só não é desenhado.

### 2.5 Voz (lacuna 5)

O plano previa STT/TTS **no cliente** ("áudio nunca sai do Mac",
`2026-11-19-serve-cliente-fino.md`, §3). Não foi implementado:
`remote_client.py` não importa `TTSManager` nem `WhisperTranscriber`.

O caminho barato: TTS local falando `run_finished.text` (o `TTSManager` é
local e não toca banco) e push-to-talk com o `WhisperTranscriber` local
mandando **texto** por `POST /turn`. O mini nunca recebe áudio.

### 2.6 Trava de escrita (lacuna 6)

Toda escrita no vault passa por um único lugar: `SecondBrain`
(`gaia/secondbrain/notes.py`). Os chamadores são poucos e conhecidos:
`consolidation.py:249-250`, `dream.py:518-523`, `registration.py:25,52`,
`controller/memory.py:239`.

Proposta: `SecondBrainConfig.read_only` (+ `GAIA_SB_READ_ONLY`), checado em
`create_note`, `update_note`, `delete_note` e `append_preference`, com mensagem
explícita ("esta máquina é cliente; use `gaia --remote`"). No Mac:
`GAIA_SB_READ_ONLY=true`. No mini, o default (`false`).

Complementos da mesma trava, porque `read_only` no vault não cobre o resto:

- `gaia.db`: o Mac ainda abre e escreve o banco local em `gaia` sem
  `--remote`. Silêncio aqui é proposital? Não — `gaia doctor` deveria mostrar
  qual motor está em uso.
- `gaia index` / `search` / `note` (`cli_commands.py:49-65`) abrem
  `sb_index.db` local. Com o Mac thin, o índice local fica velho e a busca
  local diverge da do mini.
- `gaia doctor` ganha uma linha: "motor: mini (cliente) · Second Brain:
  somente leitura".

---

## 3. Ordem recomendada

| Pacote | Conteúdo | Lacunas | Esforço | Destrava |
|---|---|---|---|---|
| **A** | fix de `_dream_catch_up` (teste sensível ao horário) | — | ~20 min | CI vermelho |
| **B** | `ui.confirm` no fio | 3 | ~30 min | bug latente |
| **C** | render dos eventos que faltam + `command_output` | 4 | ~1 h | comandos remotos |
| **D** | `gui/remote_controller.py` + rotas da TUI | 1, 2 | ~1 dia | TUI remota |
| **E** | voz no cliente (TTS local + push-to-talk) | 5 | ~meio dia | falar sem abrir o motor local |
| **F** | `secondbrain.read_only` + doctor | 6 | ~1–2 h | fim da divergência |

Não há dependência entre A/B/C e D/E/F: os três primeiros são correções
isoladas e testáveis; os três últimos são a migração em si.

### A — `_dream_catch_up` (fora do escopo, mas está vermelho)

`tests/test_orchestrator.py::test_catch_up_sonha_so_de_manha` falha entre 02:00
e 08:00 porque o teste assume que "de manhã" está fora da janela de sonho,
enquanto `_dream_catch_up` lê o relógio real (`memory.py:159`) e sai cedo
dentro da janela (`memory.py:161-162`).

A correção certa **não** é `monkeypatch` no relógio: é alinhar a API. Os
vizinhos já recebem `now` (`_maybe_dream(now)`, `_maybe_dream_report(now)`);
`_dream_catch_up` é a exceção. Assinatura passa a
`_dream_catch_up(self, now: datetime | None = None)`, ajustando
`contract.py:181` e a chamada em `memory.py:194` (que já tem `now` calculado).
O teste fica determinístico com quatro casos: 03:00 dentro da janela (nada),
09:00 fora da janela (sonha ontem), 15:00 (nada), dia já sonhado/desligado.

### F — detalhe da trava

Onde `read_only` dói de fato é no passo 4 da §0: `consolidate_day` roda no
boot e **sobrescreve**. Com a trava, o Mac levanta erro claro em vez de perder
texto — mas o worker de memória não pode derrubar o app por isso:

- `MemoryMixin._memory_loop` já envolve `_consolidate_previous_day` e
  `_dream_catch_up` em `try/except` com `logger.warning` (`memory.py:190-196`),
  então o erro aparece no log e o app continua. Bom.
- `_maybe_consolidate` / `_maybe_dream` também estão no `try` do laço
  (`memory.py:198-211`). Bom.
- Falta não repetir a tentativa a cada 60 s: logar uma vez e desligar o worker
  quando `read_only` estiver ligado, em vez de gerar 60 warnings por hora.

Alternativa mais suave (não recomendada como principal): primeiro-escritor-vence
— antes de escrever, olhar o frontmatter da nota do dia e pular se veio de
outro host. Converte perda silenciosa em skip visível, mas **não** elimina a
divergência: o Mac deixa de registrar o próprio dia. Serve como cinto de
segurança durante a migração, não como solução.

---

## 4. Decisões e alternativas descartadas

| Decisão | Escolha | Por que não a alternativa |
|---|---|---|
| Onde fica o motor | **mini** (já é o dono de fato) | Mac local com sync de texto: perda silenciosa (§0) |
| Como a TUI fala com o motor | adaptador cliente-side sobre rotas HTTP | embutir HTTP no `TextualApp`: acopla UI a transporte |
| Caminho de migração | A→B→C primero, D+E+F em sessão própria | tudo de uma vez: mistura correção de bug com migração de UI |
| Namespace por máquina no vault (`Memoria/<host>/`) | **descartado** | o sonho lê o vault inteiro; passaria a ler duas memórias do mesmo dia |
| Sync de banco SQLite | **descartado** (já era) | corrompe; é a razão de "um escritor só" |
| `--remote` implícito por env var no `cli.py` | preterido em favor de `[client]` no config | env var fica invisível; config é auditável por `gaia doctor` |

---

## 5. Riscos

| Risco | Mitigação |
|---|---|
| TUI remota vira segunda TUI, mantida em paralelo | adaptador só faz pass-through; nenhuma regra nova no cliente |
| Mini fora do ar = Mac sem Gaia | aceito explicitamente; `gaia doctor` avisa e o `--remote` tem reconexão com backoff (`remote_client.py:RECONECTAR_ESPERA`) |
| `read_only` quebrar a consolidação de saída (`on_exit`) | o `_shutdown` já captura exceção (`orchestrator.py:275-290`); logar uma vez |
| Rotas novas ampliarem a superfície de auth | todas atrás de `_autorizado()`; nada de rota pública nova além de `/health` e `/` |
| Migração parcial (D feito, F não) | F depende só de `notes.py`; pode ir antes de D |

---

## 6. Fora de escopo

- **Fila durável e compactação por janela** (fases 2/3 do plano de 19/11):
  irrelevante para uso de uma pessoa; o 409 de turno concorrente já está
  coberto por `MotorOcupado` (`app.py:26`).
- **AG-UI / cards**: o protocolo já usa nomes compatíveis onde existem
  (`remote.py:7-11`); não é preciso antecipar.
- **Múltiplos usuários, OAuth**: a fronteira é Tailscale.
- **Voz no servidor**: contraria a privacidade, que é o diferencial.
