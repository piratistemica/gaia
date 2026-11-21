# AGENTS.md — manual do agente que trabalha neste repo

Este arquivo é a fonte de verdade para agentes de código (humanos ou IA) que
mexem na Gaia. Se o código e a documentação divergirem, **o código vence** — e a
doc deve ser corrigida na mesma mudança. `README.md` é o manual do usuário;
`docs/README.md` classifica o resto da documentação.

## O que é

Gaia: assistente pessoal local em Python 3.12. Fala e ouve em pt-BR (STT
`faster-whisper` local, TTS `edge-tts`/`say`), roda um loop de agente próprio com
ferramentas, guarda memória em SQLite + Second Brain em Markdown, e é proativa
(briefing, lembretes, vagas, sonho noturno). Roda em macOS e Linux, inclusive
headless num mini PC controlado por Telegram ou por `gaia serve`.

## Comandos

```bash
# setup (o venv é obrigatório para os comandos abaixo)
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# verificação — o CI roda exatamente estes três
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy

# uso
.venv/bin/gaia                  # TUI (padrão; config interface.ui)
.venv/bin/gaia -old             # terminal Rich
.venv/bin/gaia --watch          # reinicia ao mudar .py
.venv/bin/gaia ask "..."        # turno único, sem interface
.venv/bin/gaia serve            # motor + HTTP/SSE + (opcional) Telegram
.venv/bin/gaia --remote URL     # cliente fino do serve
.venv/bin/gaia config --init    # cria ~/.config/gaia/config.toml
.venv/bin/gaia doctor           # ambiente, provedor, voz, Second Brain
```

O CI (`.github/workflows/ci.yml`) instala as deps e roda `pytest`, `ruff check
gaia tests` e `mypy` em Python 3.12. Não faça commit com qualquer um vermelho.

## Mapa do código

| Caminho | Responsabilidade |
|---|---|
| `gaia/cli.py`, `cli_parser.py`, `cli_commands.py`, `cli_integrations.py`, `cli_runtime.py`, `cli_serve.py` | entrada, subcomandos, bootstrap |
| `gaia/config.py` | modelo de config (pydantic), env `GAIA_*`, migração legada, template TOML |
| `gaia/agent/runtime.py` | loop do agente: provedor, streaming, ferramentas, permissões |
| `gaia/agent/session.py` | `AgentSession` e `SessionStore` (JSON em `sessions/`) |
| `gaia/agent/providers/` | `factory.py` (presets), `openai_compat.py`, `anthropic.py` |
| `gaia/agent/tools/` | `build_tools()` registra `read/list/glob/grep/write/edit/bash` + agenda/planilha |
| `gaia/controller/orchestrator.py` | cola entre UI e agente; conversa, sessão, workers |
| `gaia/controller/turns.py` | execução de um turno, eventos, permissão |
| `gaia/controller/commands.py` | comandos de barra (`/status`, `/modelo`, `/sonhar`…) |
| `gaia/controller/modes.py` | personas/modos (prompt, não infraestrutura) |
| `gaia/controller/prompt.py` | prompt em camadas (prefixo estável + bloco dinâmico) |
| `gaia/controller/initiative.py` | lembretes e check-in proativo; `briefing.py` | briefings |
| `gaia/controller/memory.py` | consolidação diária e sonho |
| `gaia/memory/store.py` | SQLite (`gaia.db`, schema v5: conversations/turns/records/meta/reminders) |
| `gaia/memory/consolidation.py`, `dream.py` | memória do dia e sonho |
| `gaia/secondbrain/` | índice FTS5, frontmatter, notas, embeddings opcionais |
| `gaia/briefing/compose.py` | texto dos briefings (manhã, meio-dia, noite) |
| `gaia/interface/terminal.py` | UI Rich; `textual_app.py` | TUI |
| `gaia/interface/remote.py` | `RemoteUI`: serializa eventos para o serve (fan-out + histórico) |
| `gaia/serve/server.py` | HTTP + SSE; `app.py` | ponte `OrchestratorTarget`; `web.py`/`static/` | web |
| `gaia/client/remote_client.py` | cliente `gaia --remote` |
| `gaia/telegram/`, `whatsapp/`, `browser/` | canais e integrações |
| `gaia/agenda/` | cliente da Agenda (Supabase), usado por ferramentas `agenda_*` |
| `gaia/career/`, `finance/`, `tasks/`, `focus.py` | rotinas de vagas, bolsa, tarefas, foco |

## Fluxo de um turno

1. A UI (terminal/TUI/Telegram/serve) chama `Orchestrator`.
2. `turns.py` monta o prompt via `controller/prompt.py`, registra o turno no
   `MemoryStore` e chama `AgentClient.prompt_async(session_id, ...)`.
3. `AgentClient` fala direto com a API do provedor (sem servidor intermediário),
   emite eventos (`TextDelta`, `ReasoningDelta`, `ToolStarted`,
   `PermissionAsked`…) e executa ferramentas em thread (`asyncio.to_thread`).
4. A UI consome eventos. No `serve`, `RemoteUI` serializa em `queue.Queue` e o
   transporte HTTP/SSE entrega aos clientes.

## Invariantes — não quebre

- **Um único processo escritor.** `gaia serve` é dono do `gaia.db`, do Second
  Brain, dos workers proativos e do poller do Telegram. Nunca rode dois
  processos com o mesmo banco nem dois pollers do Telegram (409 `getUpdates` e
  briefing duplicado).
- **Canais têm escopo.** O modo ativo é salvo por canal (`mode` no `cli`,
  `mode@telegram`/`mode@serve` fora dele). `channel` é `cli`, `telegram` ou
  `serve`.
- **Permissões são a fronteira de segurança.** `write`, `edit` e `bash` exigem
  aprovação. `agent.auto_approve` é opt-in. No Telegram, `auto_approve` só vale
  com `allowed_chat_id` fixado, e `bash/write/edit` continuam negados a menos
  que `telegram.shell_access = true`. Em `serve`, a decisão remota passa pelo
  `RemoteUI` (`POST /permission/{id}`).
- **Segredos só no `.env`/ambiente**, nunca no TOML. A precedência é
  ambiente/`.env` > `config.toml`. `Config.public_dict()` redige os segredos
  conhecidos (`agent.api_key`, WhatsApp, Telegram, embed e `serve.token`).
- **Dados ficam fora do repo por padrão:** `~/.local/share/gaia`
  (`GAIA_DATA_DIR`). O repo só usa `./data` quando o launcher seta
  `GAIA_DATA_DIR="$REPO/data"` (`Abrir Gaia.command`).
- **Legado:** `ZAYA_*` é espelhado para `GAIA_*` e `zaya.db` é renomeado para
  `gaia.db` na primeira execução. Não remova essa migração sem migrar os dados.
- **Second Brain:** escrever fora da raiz é proibido; editar/excluir pede
  confirmação, gera backup em `.gaia/backups` e move para `.gaia/trash`.
- **Prompt em duas camadas:** prefixo estável (persona/preferências, cacheável) +
  bloco dinâmico (data/hora/modo). Respeite isso ao adicionar contexto.
- **Estilo do repo:** UI, mensagens e testes em português; comentários em ASCII
  sem acento ("sessao", "execucao"). Evite dependência nova.

## Receitas

- **Nova ferramenta:** crie o spec/handler em `gaia/agent/tools/`, registre em
  `build_tools()` e marque `requires_approval=True` se alterar algo. Adicione
  teste em `tests/test_agent.py` e, se for específica, no arquivo do domínio.
- **Novo comando de barra:** adicione o `elif` em
  `controller/commands.py:_handle_command`, atualize `TerminalUI.help()`
  (`interface/terminal.py`) e teste em `tests/test_cli.py`/`test_orchestrator.py`.
  Se o Telegram precisar do comando, veja `gaia/telegram/service.py`.
- **Novo provedor:** edite `gaia/agent/providers/factory.py` (`PRESETS`,
  `PUBLIC_PROVIDERS`, `preset_api_key`) e documente em `.env.example` e README.
- **Novo campo de config:** adicione no modelo pydantic, em `_ENV_MAP`, no
  `_DEFAULT_CONFIG_TEMPLATE` e em `.env.example`; atualize README se for visível.
- **Novo modo:** `controller/modes.py` (`PERSONAS`) e teste em `test_modes.py`.
- **Nova automação:** `controller/briefing.py` (janelas `*_at`/`*_until`) ou
  `controller/initiative.py` (lembretes/check-in); use `claim_meta` para
  garantir "uma vez por dia".

## Armadilhas já descobertas

- **SSE:** assine a fila **antes** de enviar os cabeçalhos; o cliente dispara no
  header e o evento se perde para sempre.
- **Permissão:** quem espera é o `RemoteUI`, não o `Target`; o transporte resolve
  a UI primeiro e só depois avisa o alvo (senão trava até o timeout).
- **`ui.permission` precisa de `request_id`**; sem ele, nega por padrão.
- **Texto final do turno** no `serve`/web vem em `run_finished.text`; o
  `stream_end` pode chegar sem texto (provedor sem deltas).
- **Reindex no start** roda antes de `servidor.start()`; com índice frio o HTTP
  fica sem resposta por minutos. Evite `reindex_on_start` no serve.
- **SIGTERM leva ~13 s** (worker de memória com `shield`); cada deploy paga isso.
- **`bash` não é sandbox:** o guard de caminho cobre `read/write/edit/list/glob/
  grep`, não o shell. Hoje o env do subprocesso herda o `.env` inteiro (chaves e
  tokens) — trate como pendência de segurança.
- **`always` de permissão** hoje é global ao processo, não à sessão (pendência
  registrada em `docs/prompt-correcoes.md`).
- **`bash` herda o `.env` inteiro** hoje; não use a ferramenta para inspecionar
  ambiente/segredos até isso ser filtrado (pendência).

## Testes

- `pytest` com `asyncio_mode = "auto"`; use `tmp_path` para dados e fakes para
  rede/provedor. A suíte não toca microfone, rede nem baixa modelos.
- Padrões úteis: `tests/test_agent.py` (ferramentas/provider),
  `tests/test_orchestrator.py` (fluxo), `tests/test_serve_app.py` (thread↔loop),
  `tests/test_telegram.py` (permissões), `tests/test_config.py` (env/TOML).
- Para bug, escreva o teste que reproduz antes de corrigir; para permissão,
  teste os dois lados (auto_approve e shell_access).

## Status e backlog

- `docs/prompt-correcoes.md` é uma **spec ativa ainda não aplicada**: lembretes
  perdidos, `always` por sessão e truncamento do bash mantendo o fim.
- Segurança pendente: sandbox/env do `bash`, lock no `POST /turn`, aprovação
  remota vs. callback do Telegram, redação de `serve.token`, timeout de
  permissão.
- `docs/plans/2026-11-20-handoff.md` tem o estado operacional e o backlog do
  serve (fila durável, compactação por janela real, reindex fora do start).
- Memória: schema v5 no código; o schema v6 (`facts`, `recall`, check-in 18h) de
  `docs/memoria-plano.md` e `docs/memoria-checkin-18h.md` **não** está
  implementado.
