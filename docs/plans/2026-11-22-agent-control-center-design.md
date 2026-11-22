# Agent Control Center — design

> **Status: aprovado, pronto para implementar (Fases A e B).**
> Fases C, D e E estão em `2026-11-22-control-center-fases-cde.md`.
> Referência atemporal: `../../AGENTS.md`.

## 1. Objetivo

Um painel web (`/control`) servido pelo **próprio `gaia serve`** para
configurar, supervisionar e diagnosticar o agente sem editar TOML nem rodar
comandos. Não é um protótipo visual: tudo que aparece vem de estado real do
motor.

Restrição que manda em tudo: **o painel é uma camada de gerência sobre o
núcleo existente**. Não cria um segundo agendador, não cria um segundo banco e
não duplica segredos.

## 2. Auditoria — o que já existe e será reaproveitado

| Necessidade | O que já existe | Como o painel usa |
|---|---|---|
| transporte HTTP | `serve/server.py` (`_Handler`, `ServeServer`) | novas rotas `/control/api/*` no mesmo handler |
| página web sem build | `serve/web.py` + `serve/static/index.html` | mesmo padrão (HTML+CSS+JS versionados) |
| estado do motor | `serve/app.py:OrchestratorTarget` | novas leituras no `Target` |
| provedores | `agent/providers/factory.py` (`PRESETS`, `resolve_api_key`, `describe_provider`) | listar, testar, trocar |
| catálogo de modelos | `agent/catalog.py:collect_models` | aba Models (já usado por `/models`) |
| troca de modelo | `Orchestrator.model_messages` + `save_agent_model` | `POST /control/api/model` |
| segredos | `.env` (`Config.default_dotenv_path`, `_load_dotenv`) | leitura mascarada e escrita atômica |
| config | `config.py` (`_ENV_MAP`, `_apply_env`, `_agent_section_bounds`, `_replace_agent_key`) | edição TOML com origem por campo |
| idioma de execução | `store.claim_meta` (uma vez por dia) | reserva preservada; **não** reimplementar |
| banco | `memory/store.py` (schema v5, RLock, WAL) | `runs` no **mesmo** SQLite |
| saúde | `/health` existente | expandido com componentes |
| logs | `logging_setup.py` (`gaia.log`) | último erro por skill, link para o log |

**Não existe hoje (e o design não finge que existe):**

- fallback de modelo em caso de falha no turno;
- histórico persistido de execuções de rotinas;
- conceito de "skill" como registro consultável;
- detecção de automação "que deveria ter rodado e não rodou".

Os três primeiros entram na Fase A. O último também, sem tocar no agendador
(ver §6).

## 3. Decisões fechadas

| # | Decisão | Motivo |
|---|---|---|
| D1 | Painel web dentro do `gaia serve` | um processo escritor, acesso remoto, zero infra nova |
| D2 | Secrets continuam no `.env` (chmod 600) | invariante do repo; backup + releitura conferida |
| D3 | Skill = descritor declarativo que delega ao código existente | não reescreve `career/`, `finance/`, `agenda/`, `estudos/` |
| D4 | Fase A **observa**, Fase C **governa** | o `claim_meta` que evita entrega dupla não é tocado agora |
| D5 | Config em TOML (edição cirúrgica, com `.bak` e conferência) | uma fonte de verdade; não reintroduz o problema do `.zshrc` |
| D6 | Fallback mínimo: só erros de credencial/saldo (401/402/403) | resolve o 402 do mini sem inventar política de retry |
| D7 | `GAIA_CONTROL_TOKEN` separado e obrigatório | token de chat não vira chave administrativa |
| D8 | Falha notifica no Telegram só se a automação entrega no Telegram | avisa quando o silêncio seria confundido com "nada aconteceu" |
| D9 | Só registra execução quando a rotina **realmente executou** | "aguardando horário" não é execução |

## 4. Arquitetura

```
                           ┌──────────────────────────────┐
  browser ── /control ────▶│ ServeServer (http.server)    │
  Bearer CONTROL_TOKEN     │  /control/api/*              │
                           └───────────┬──────────────────┘
                                       │ Target (thread)
                           ┌───────────▼──────────────────┐
                           │ OrchestratorTarget           │
                           │  run_coroutine_threadsafe    │
                           └───────────┬──────────────────┘
                                       │ asyncio
   ┌───────────────────────────────────▼─────────────────────────────────┐
   │ Orchestrator (loop único, dono do banco e dos workers)              │
   │  _proactive_loop  _briefing_loop  _career_loop  _memory_loop        │
   │        │                │              │             │              │
   │        └────────────────┴──────┬───────┴─────────────┘              │
   │                                │ record_run() (novo, aditivo)       │
   └────────────────────────────────┼────────────────────────────────────┘
                                    │
              ┌─────────────────────▼─────────────────────┐
              │ gaia/control/  (novo pacote, sem estado)  │
              │  runs · skills · automations · health     │
              │  settings · secrets · providers           │
              └─────────────────────┬─────────────────────┘
                                    │
              ┌─────────────────────▼─────────────────────┐
              │ MemoryStore (SQLite, schema v6) + .env    │
              │ + config.toml                             │
              └───────────────────────────────────────────┘
```

`gaia/control/` é **lógica pura** (sem `asyncio` obrigatório, sem HTTP): recebe
`Config`, `MemoryStore`, um cliente de agente e devolve dicionários prontos para
JSON. Testável com `tmp_path` e fakes, como o resto do repo.

## 5. Componentes

### 5.1 `gaia/control/runs.py` — histórico de execuções

A tabela vive no SQLite existente (schema v6), acessada pelos métodos novos do
`MemoryStore` para não furar o lock/WAL:

```sql
CREATE TABLE runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,      -- skill: career, market, briefing, agenda...
    automation    TEXT NOT NULL DEFAULT '',  -- career:weekly, briefing:morning...
    trigger       TEXT NOT NULL,      -- schedule | manual | retry
    status        TEXT NOT NULL,      -- running | completed | failed | cancelled
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    duration_ms   INTEGER,
    delivery      TEXT NOT NULL DEFAULT '', -- telegram | local | none
    detail        TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    error_kind    TEXT NOT NULL DEFAULT '', -- auth | network | config | unknown
    attempt       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_runs_started ON runs(started_at DESC);
CREATE INDEX idx_runs_kind    ON runs(kind, started_at DESC);
CREATE INDEX idx_runs_status  ON runs(status, started_at DESC);
```

`record_run()` é um `asynccontextmanager` async; usado **depois** do gate de
horário, envolvendo só a execução real (D9):

```python
async with record_run(self.store, kind="career", automation="career:weekly",
                      delivery="telegram") as run:
    ...            # corpo da rotina
```

Falha ao registrar **nunca** pode derrubar a rotina (`except Exception: log`).

### 5.2 `gaia/control/skills.py` — catálogo

Descritor imutável, sem lógica de negócio:

```python
@dataclass(frozen=True)
class Skill:
    id: str            # career
    name: str          # "Pesquisa de vagas"
    description: str
    kind: str          # routine | tool_group
    requires: tuple[str, ...]      # career.enabled, TELEGRAM_BOT_TOKEN...
    provides: tuple[str, ...]      # tools expostas: agenda_*, estudos_*
    schedule_hint: str             # "seg 09:00-18:00" (legível)
```

Registry em código (`SKILLS`), derivado do `Config`: `enabled` **não** é campo
do descritor — é calculado (`skill_status(cfg, store)`) para não haver duas
fontes de verdade. Skills iniciais: `career`, `market`, `agenda`, `estudos`,
`briefing`, `memory`, `dream`, `audiobook`, `planilhas`, `tools`.

Adicionar skill nova = uma entrada no registry (+ handler, se for `routine`).
O painel não muda (requisito de extensibilidade).

### 5.3 `gaia/control/automations.py` — agenda declarada + estado

Descreve **quando** cada rotina deve rodar, lendo os mesmos campos de config que
os loops usam hoje (`career.days/at/until`, `briefing.morning_at`, …). Não
executa nada (Fase A).

```python
@dataclass(frozen=True)
class Automation:
    id: str            # career:weekly
    skill: str         # career
    days: tuple[int, ...]
    at: str
    until: str
    delivery: str      # telegram | local

def describe(cfg) -> list[Automation]
def next_run(a: Automation, now: datetime) -> datetime | None
def status(a, cfg, store, last_run, now) -> str
```

`status` devolve um dos valores exigidos pelo pedido:

- `disabled` — config desligou (`career.enabled = false`);
- `paused` — pausada pelo painel (Fase C; na A nunca ocorre);
- `scheduled` — próxima execução no futuro;
- `running` — existe run aberto;
- `completed` / `failed` — último run;
- `missed` — **a chave de `claim_meta` da janela não existe e o horário já
  passou** (o requisito "deveria ter rodado e não rodou"), com tolerância
  configurável.

Isso é calculado comparando a agenda declarada com `meta` + `runs`: **zero
mudança no agendador**.

### 5.4 `gaia/control/health.py` — System Health

Um dicionário, cada item com `status` (`ok|warn|error|off`), `detail` e
`checked_at`:

- **engine**: uptime, `busy`, modelo ativo;
- **database**: caminho, tamanho, `schema_version`, WAL;
- **second_brain**: raiz existe, nº de notas no índice, idade do índice;
- **providers**: chave presente (nunca o valor), último erro conhecido;
- **telegram**: habilitado, `allowed_chat_id` definido, último poll com erro;
- **scheduler**: workers vivos (tasks não `done()`), próxima execução;
- **config**: overrides do `.env` que contradizem o TOML (a causa do incidente
  de 22/11).

### 5.5 `gaia/control/secrets.py` — credenciais

- `list_secrets(env_path)` → `[{key, masked, source, updated_at}]`, máscara
  `sk-…abcd` (4 últimos caracteres; nunca o valor).
- `set_secret(env_path, key, value)` → backup `.env.bak`, escreve, `chmod 600`,
  re-lê e confirma.
- `remove_secret(env_path, key)` → comenta a linha (não apaga), backup.
- **Nunca** escreve em `config.toml`, nunca loga o valor, e o `public_dict()`
  continua redigindo.
- Só aceita chaves da allowlist (`_PROVIDER_API_KEYS` + `GAIA_AGENT_API_KEY` +
  `GAIA_SB_EMBED_API_KEY` + `GAIA_SERVE_TOKEN` + `GAIA_CONTROL_TOKEN` +
  `GAIA_TELEGRAM_BOT_TOKEN`). Qualquer outra → 400.

### 5.6 `gaia/control/settings.py` — config com origem

```python
def effective(cfg, env) -> list[Setting]   # section, key, value, source, editable
def update(section, key, value, path) -> None
```

`source` ∈ `default | toml | env`. Se `env`, `editable=False` com hint
"definido por GAIA_X no .env". `update` usa `_agent_section_bounds`
generalizado (`_section_bounds(lines, name)`), grava, **re-lê com `tomllib`** e
valida com `Config.model_validate`; qualquer divergência restaura o `.bak` e
levanta `ValueError`. Suporta `str/int/float/bool` e listas numa linha
(`market_days = [0, 2, 4]`).

### 5.7 Fallback mínimo de modelo (`gaia/agent/`)

Campo novo em `AgentConfig`: `fallback_models: list[str] = []` (slugs
`provider/model`, máximo 3). No `runtime._run_turn`, a chamada ao provider passa
por um helper que, em `ProviderError` com `status_code in {401, 402, 403}`:

1. emite evento novo `ModelFallback(from_slug, to_slug, reason, status_code)`;
2. constrói o provider do alternativo (`build_provider(cfg.model_copy(update=...))`);
3. repete **uma** vez; se também falhar, propaga o erro original com nota.

Nada de retry em 429/5xx nesta fase (D6). A troca aparece em Activity; o modelo
ativo **não** é alterado no config (fallback é por turno).

### 5.8 Rotas

| Método | Rota | Função |
|---|---|---|
| GET | `/control` | HTML do painel (público, pede token na tela) |
| GET | `/control/api/overview` | resumo do Overview |
| GET | `/control/api/health` | System Health |
| GET | `/control/api/skills` | catálogo + status |
| GET | `/control/api/automations` | agenda + status (inclui `missed`) |
| GET | `/control/api/runs?limit&kind&status` | histórico |
| GET | `/control/api/providers` | presets, credencial, modelo atual |
| POST | `/control/api/providers/{name}/test` | teste de conexão real |
| POST | `/control/api/model` | troca de modelo (delega `model_messages`) |
| GET | `/control/api/settings` | config efetiva com origem |
| PATCH | `/control/api/settings` | edita TOML com validação |
| GET | `/control/api/secrets` | chaves mascaradas |
| PUT | `/control/api/secrets/{key}` | grava/rotaciona |
| DELETE | `/control/api/secrets/{key}` | comenta a linha (remove override) |

Autenticação: `Authorization: Bearer $GAIA_CONTROL_TOKEN` em **todas** as rotas
`/control/api/*`. Sem `GAIA_CONTROL_TOKEN` definido, `/control` responde 503 com
instrução e nenhuma rota de API existe (nunca herda `serve.token`).

### 5.9 UI

`gaia/control/static/{index.html,style.css,app.js}` — sem build, mesma paleta e
tipografia do chat (`--bg/--panel/--accent`). Abas:

1. **Overview** — pills de saúde, modelo ativo, automações ativas, últimas 10
   execuções, alerta de override no `.env`.
2. **Models & Providers** — presets com estado da credencial, teste de conexão
   (mostra 401/402/timeout reais), troca de modelo, fast/summary/fallback.
3. **Skills** — catálogo somente leitura na Fase A (ações em C).
4. **Automations** — agenda com `next_run` e status, incluindo `missed`.
5. **Activity** — tabela filtrável por skill/status, detalhe técnico expansível.
6. **Settings** — seções do config, campo travado quando vem do `.env`, botão
   "remover override".

Token no `localStorage`, enviado em header (`fetch`), nunca na URL.

## 6. O que **não** muda

- `store.claim_meta` e o gate de horário de cada loop (D4).
- Um único processo escritor; nada de scheduler novo.
- Canais (`cli`/`telegram`/`serve`) e precedência de config.
- `public_dict()` continua redigindo segredos.
- Comportamento do Telegram se `GAIA_CONTROL_TOKEN` não existir.

## 7. Erros e degradação

| Situação | Comportamento |
|---|---|
| `GAIA_CONTROL_TOKEN` ausente | `/control` 503 explicando; API inexistente |
| provider sem chave | `providers` mostra `missing_key`; teste responde 400 com motivo |
| `.env` sem permissão | `secrets` mostra `read_only`; escrita 403 |
| TOML inválido após escrita | restaura `.bak`, 422 com diff do erro |
| banco travado | `runs`/`health` degradam com `warn`, sem derrubar o serve |
| falha ao registrar run | log `warning`, rotina segue |

## 8. Testes (CI: `pytest`, `ruff`, `mypy`)

- `tests/test_control_runs.py` — begin/finish/fail, filtros, resumo, schema v6.
- `tests/test_control_skills.py` — catálogo derivado do config, status.
- `tests/test_control_automations.py` — `next_run`, `missed` com/sem `claim_meta`.
- `tests/test_control_health.py` — cada item ok/warn/error com fakes.
- `tests/test_control_secrets.py` — máscara, chmod, backup, allowlist, remoção.
- `tests/test_control_settings.py` — origem, escrita preservando comentários,
  rollback em TOML inválido, listas.
- `tests/test_control_api.py` — todas as rotas, auth obrigatório, 503 sem token.
- `tests/test_agent_fallback.py` — 402 troca de modelo, evento emitido, 500 não.
- `tests/test_runs_instrumentation.py` — `career`/`briefing` registram run só
  quando executam.

## 9. Não-objetivos (explícitos)

- Pausar/executar automação pelo painel → Fase C.
- CRUD de skills e histórico de execução por skill → Fase C.
- Retry automático com backoff → Fase C.
- Notificação de falha no Telegram → Fase C (D8 definida, implementação depois).
- Níveis de autonomia e confirmação de ação sensível → Fase D.
