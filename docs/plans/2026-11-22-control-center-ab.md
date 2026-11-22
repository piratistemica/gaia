# Agent Control Center — Fases A e B: plano de implementação

> **Para quem executa:** use `superpowers:executing-plans` tarefa por tarefa.
> Ler antes: `AGENTS.md`, `2026-11-22-agent-control-center-design.md`.
> Fases C/D/E: `2026-11-22-control-center-fases-cde.md` (não fazer agora).

**Goal:** entregar um Control Center web servido pelo `gaia serve`, com dados
reais: modelos/providers, secrets mascarados, config editável com origem,
histórico de execuções das rotinas existentes e saúde do sistema.

**Arquitetura:** pacote novo `gaia/control/` (lógica pura, sem HTTP) + rotas
`/control/api/*` no `ServeServer` existente + estáticos sem build. O agendador
**não muda**: `record_run()` é aditivo e envolve só a execução real.

**Tech stack:** Python 3.12, stdlib (`http.server`, `sqlite3`, `tomllib`),
pydantic, httpx (já existente), HTML/CSS/JS sem bundler.

**Regra do repo:** rodar os três verificadores antes de cada commit:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

---

## Ordem das tarefas

| # | Tarefa | Arquivos principais |
|---|---|---|
| 1 | Schema v6: tabela `runs` + métodos | `gaia/memory/store.py` |
| 2 | `control/runs.py` (contexto + consultas + resumo) | `gaia/control/runs.py` |
| 3 | `control/skills.py` (registry + status) | `gaia/control/skills.py` |
| 4 | `control/automations.py` (`next_run`, `status`, `missed`) | `gaia/control/automations.py` |
| 5 | `control/health.py` | `gaia/control/health.py` |
| 6 | `control/secrets.py` | `gaia/control/secrets.py` |
| 7 | `control/settings.py` (origem + escrita TOML) | `gaia/control/settings.py` |
| 8 | `control/providers.py` (listar, testar, trocar) | `gaia/control/providers.py` |
| 9 | Fallback mínimo no runtime | `gaia/config.py`, `gaia/agent/runtime.py`, `events.py` |
| 10 | Instrumentação aditiva das rotinas | `controller/{career,briefing,memory}.py` |
| 11 | Rotas `/control/api/*` + auth | `gaia/serve/server.py`, `gaia/serve/app.py` |
| 12 | UI estática do painel | `gaia/control/static/*` |
| 13 | CLI/doctor + docs | `cli_serve.py`, `README.md`, `.env.example`, `AGENTS.md`, `docs/README.md` |

---

## Tarefa 1 — Schema v6: tabela `runs`

**Files:**
- Modify: `gaia/memory/store.py` (`SCHEMA_VERSION`, `_init_schema`, `_migrate`)
- Test: `tests/test_memory.py`

**Step 1: escreva o teste que falha**

```python
def test_schema_v6_cria_tabela_runs(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        assert store.schema_version() == 6
        cols = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        assert {"kind", "automation", "trigger", "status", "started_at",
                "finished_at", "duration_ms", "delivery", "error",
                "error_kind", "attempt"} <= cols
    finally:
        store.close()


def test_migracao_v5_para_v6_preserva_dados(tmp_path):
    path = tmp_path / "gaia.db"
    store = MemoryStore(path)
    store.add_reminder("beber agua", "2026-11-23T10:00:00")
    store.set_meta("schema_version", "5")
    with store._conn:
        store._conn.execute("DROP TABLE runs")
    store.close()

    store = MemoryStore(path)
    try:
        assert store.schema_version() == 6
        assert len(store.pending_reminders()) == 1
    finally:
        store.close()
```

**Step 2: rode e veja falhar**

`pytest tests/test_memory.py -k runs -v` → `assert 5 == 6`.

**Step 3: implemente**

Em `gaia/memory/store.py`: `SCHEMA_VERSION = 6`; adicione o `CREATE TABLE
IF NOT EXISTS runs (...)` com os três índices no `executescript` de
`_init_schema` (idempotente, então bancos v5 ganham a tabela na abertura), e
`set_meta("schema_version", "6")` no fim de `_migrate`.

**Step 4: verde**

`pytest tests/test_memory.py -v` e depois a suíte inteira.

**Step 5: commit**

```bash
git add gaia/memory/store.py tests/test_memory.py
git commit -m "feat(memory): schema v6 com tabela runs para historico de execucoes"
```

---

## Tarefa 2 — `gaia/control/runs.py`

**Files:**
- Create: `gaia/control/__init__.py`, `gaia/control/runs.py`
- Modify: `gaia/memory/store.py` (métodos `begin_run`, `finish_run`,
  `list_runs`, `runs_summary`, `last_run`)
- Test: `tests/test_control_runs.py`

**Step 1: teste**

```python
async def test_record_run_sucesso(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        async with record_run(store, kind="career", automation="career:weekly",
                              delivery="telegram") as run:
            run.detail = "3 vagas"
        rows = store.list_runs()
        assert len(rows) == 1
        assert rows[0]["status"] == "completed"
        assert rows[0]["duration_ms"] >= 0
        assert rows[0]["detail"] == "3 vagas"
    finally:
        store.close()


async def test_record_run_falha_registra_erro(tmp_path):
    store = MemoryStore(tmp_path / "gaia.db")
    try:
        with pytest.raises(RuntimeError):
            async with record_run(store, kind="market"):
                raise RuntimeError("boom")
        row = store.list_runs()[0]
        assert row["status"] == "failed"
        assert "boom" in row["error"]
        assert row["error_kind"] == "unknown"
    finally:
        store.close()


def test_classifica_erro_de_credencial(tmp_path):
    assert classify_error("401 Unauthorized") == "auth"
    assert classify_error("402 Insufficient Balance") == "auth"
    assert classify_error("timeout ao falar com o provedor") == "network"
    assert classify_error("career.enabled nao definido") == "config"
    assert classify_error("qualquer outra coisa") == "unknown"
```

**Step 2: falha** → `ModuleNotFoundError: gaia.control`.

**Step 3: implemente**

Store (dentro do `RLock`, mesma conexão):

```python
def begin_run(self, *, kind, automation="", trigger="schedule",
              delivery="", attempt=1) -> int: ...
def finish_run(self, run_id, *, status, detail="", error="",
               error_kind="") -> None: ...   # calcula duration_ms
def list_runs(self, limit=50, kind="", status="") -> list[dict]: ...
def last_run(self, kind: str, automation: str = "") -> dict | None: ...
def runs_summary(self, since_iso: str) -> dict: ...
```

`gaia/control/runs.py`: `RunHandle` (dataclass mutável com `detail`), o
`@asynccontextmanager record_run(...)` que nunca deixa a falha de registro
derrubar a rotina (`except Exception: logger.warning(...)`),
`classify_error(text)` e `render_run(row)` (dict pronto para JSON, datas ISO).

**Step 4: verde** `pytest tests/test_control_runs.py -v`.

**Step 5: commit** `feat(control): registro de execucoes das rotinas`.

---

## Tarefa 3 — `control/skills.py`

**Files:**
- Create: `gaia/control/skills.py`
- Test: `tests/test_control_skills.py`

**Step 1: teste**

```python
def test_registry_tem_as_skills_iniciais():
    ids = {skill.id for skill in SKILLS}
    assert {"career", "market", "agenda", "estudos", "briefing",
            "memory", "dream", "audiobook", "planilhas", "tools"} <= ids


def test_status_reflete_config_e_credencial(cfg_base, store):
    lista = {item["id"]: item for item in describe_skills(cfg_base, store)}
    assert lista["career"]["status"] == "active"
    cfg_base.career.enabled = False
    lista = {item["id"]: item for item in describe_skills(cfg_base, store)}
    assert lista["career"]["status"] == "disabled"


def test_skill_sem_credencial_fica_err(cfg_base, store):
    cfg_base.telegram.enabled = False
    lista = {item["id"]: item for item in describe_skills(cfg_base, store)}
    assert lista["career"]["status"] == "error"
    assert "telegram" in lista["career"]["reason"].lower()


def test_ultima_execucao_aparece(cfg_base, store):
    store.begin_run(kind="career", automation="career:weekly")
    lista = {item["id"]: item for item in describe_skills(cfg_base, store)}
    assert lista["career"]["last_run"] is not None
```

**Step 2: falha.**

**Step 3: implemente**

```python
@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    kind: str                    # routine | tool_group
    requires: tuple[str, ...]    # "career.enabled", "telegram.enabled"
    provides: tuple[str, ...] = ()
    schedule_hint: str = ""

SKILLS: tuple[Skill, ...] = (...)

def describe_skills(cfg, store) -> list[dict]:
    """status: active | disabled | error, com reason e last_run."""
```

`requires` é avaliado sobre `cfg` por `getattr` encadeado; ausência de
credencial vira `error` com motivo legível. Sem cache: é barato e sempre fresco.

**Step 4 e 5:** verde + commit `feat(control): catalogo de skills`.

---

## Tarefa 4 — `control/automations.py`

**Files:**
- Create: `gaia/control/automations.py`
- Test: `tests/test_control_automations.py`

**Step 1: teste**

```python
def test_next_run_da_rotina_de_vagas(cfg_base):
    cfg_base.career.days = [0]
    cfg_base.career.at = "09:00"
    item = next(a for a in describe(cfg_base) if a.id == "career:weekly")
    # segunda 24/11/2026 08:00 -> proxima segunda 09:00
    assert next_run(item, datetime(2026, 11, 24, 8, 0).astimezone()).date() == \
        datetime(2026, 11, 30).date()


def test_status_missed_quando_horario_passou_sem_claim(cfg_base, store):
    cfg_base.career.days = [0]
    cfg_base.career.at = "00:01"
    cfg_base.career.until = "23:59"
    item = next(a for a in describe(cfg_base) if a.id == "career:weekly")
    # segunda-feira, depois da janela, sem meta 'career:...'
    now = datetime(2026, 11, 23, 20, 0).astimezone()
    assert status(item, cfg_base, store, now) == "missed"


def test_status_scheduled_quando_ainda_nao_e_a_hora(cfg_base, store):
    cfg_base.career.days = [0]
    cfg_base.career.at = "23:30"
    item = next(a for a in describe(cfg_base) if a.id == "career:weekly")
    now = datetime(2026, 11, 23, 8, 0).astimezone()
    assert status(item, cfg_base, store, now) == "scheduled"


def test_status_disabled(cfg_base, store):
    cfg_base.career.enabled = False
    item = next(a for a in describe(cfg_base) if a.id == "career:weekly")
    assert status(item, cfg_base, store, datetime(2026, 11, 23, 20, 0).astimezone()) \
        == "disabled"
```

**Step 2: falha.**

**Step 3: implemente**

`describe(cfg)` devolve as automações declaradas: `career:weekly`,
`briefing:morning`, `briefing:noon`, `briefing:evening`, `market:watchlist`,
`memory:daily`, `memory:dream`. Reaproveite as funções puras de
`gaia/briefing/compose.py` e `gaia/career/service.py` para decidir a janela (não
reimplemente a regra). `status(...)` segue o design §5.3; `missed` compara o
fim da janela com `now` e a existência da chave em `meta` **e** de um run
`completed` no dia — sem tocar no agendador.

**Step 4 e 5:** verde + commit `feat(control): agenda declarada das automacoes`.

---

## Tarefa 5 — `control/health.py`

**Files:**
- Create: `gaia/control/health.py`
- Test: `tests/test_control_health.py`

**Step 1: teste**

```python
def test_health_tem_os_componentes(cfg_base, store, tmp_path):
    cfg_base.secondbrain.path = str(tmp_path / "sb")
    (tmp_path / "sb").mkdir()
    itens = system_health(cfg_base, store, orch=None, providers=[])
    chaves = {item["id"] for item in itens}
    assert {"engine", "database", "second_brain", "providers",
            "telegram", "scheduler", "config"} <= chaves
    for item in itens:
        assert item["status"] in {"ok", "warn", "error", "off"}
        assert item["detail"]


def test_health_aponta_override_do_env(cfg_base, store, monkeypatch):
    monkeypatch.setenv("GAIA_PROACTIVE_IDLE_CHECKIN", "false")
    cfg_base.proactive.idle_checkin = True
    item = next(i for i in system_health(cfg_base, store, orch=None, providers=[])
                if i["id"] == "config")
    assert item["status"] == "warn"
    assert "GAIA_PROACTIVE_IDLE_CHECKIN" in item["detail"]
```

**Step 2 → 5:** TDD; depois commit `feat(control): system health`.

---

## Tarefa 6 — `control/secrets.py`

**Files:**
- Create: `gaia/control/secrets.py`
- Test: `tests/test_control_secrets.py`

**Step 1: teste**

```python
def test_mascara_nao_expoe_valor(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-segredo-1234\n")
    item = list_secrets(env)[0]
    assert item["key"] == "OPENAI_API_KEY"
    assert item["masked"] == "sk-…1234"
    assert "segredo" not in json.dumps(item)


def test_set_secret_grava_com_backup_e_chmod(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OUTRA=1\n")
    set_secret(env, "OPENAI_API_KEY", "sk-nova-9876")
    assert "OPENAI_API_KEY=\"sk-nova-9876\"" in env.read_text()
    assert (tmp_path / ".env.bak").exists()
    assert oct(env.stat().st_mode)[-3:] == "600"


def test_set_secret_fora_da_allowlist_falha(tmp_path):
    with pytest.raises(ValueError):
        set_secret(tmp_path / ".env", "AWS_SECRET", "x")


def test_remove_secret_comenta_linha(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-1\nPATH_X=2\n")
    remove_secret(env, "OPENAI_API_KEY")
    texto = env.read_text()
    assert "# OPENAI_API_KEY=sk-1" in texto
    assert "PATH_X=2" in texto
```

**Step 2 → 5:** TDD. Allowlist explícita (design §5.5). Nunca logar valor.
Commit `feat(control): gerenciamento de secrets no .env`.

---

## Tarefa 7 — `control/settings.py`

**Files:**
- Create: `gaia/control/settings.py`
- Modify: `gaia/config.py` (extrair `_section_bounds` de `_agent_section_bounds`
  e manter o wrapper antigo para não quebrar chamadas)
- Test: `tests/test_control_settings.py`

**Step 1: teste**

```python
def test_effective_marca_origem(tmp_path, monkeypatch):
    toml = tmp_path / "config.toml"
    toml.write_text('[proactive]\nidle_minutes = 30.0\n')
    cfg = Config.load(config_path=toml, env={})
    monkeypatch.setenv("GAIA_PROACTIVE_DAILY_MAX", "9")
    itens = {(i["section"], i["key"]): i for i in effective(cfg, os.environ)}
    assert itens[("proactive", "idle_minutes")]["source"] == "toml"
    assert itens[("proactive", "idle_minutes")]["editable"] is True
    assert itens[("proactive", "daily_max")]["source"] == "env"
    assert itens[("proactive", "daily_max")]["editable"] is False


def test_update_preserva_comentarios(tmp_path, cfg_base):
    toml = tmp_path / "config.toml"
    toml.write_text("# comentario\n[proactive]\nidle_minutes = 45.0  # padrao\n")
    update(toml, "proactive", "idle_minutes", 30.0)
    texto = toml.read_text()
    assert "# comentario" in texto
    assert "# padrao" in texto
    assert "idle_minutes = 30.0" in texto


def test_update_lista_em_uma_linha(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[career]\ndays = [0]\n")
    update(toml, "career", "days", [0, 2])
    assert "days = [0, 2]" in toml.read_text()


def test_update_invalido_restaura_backup(tmp_path):
    toml = tmp_path / "config.toml"
    original = '[agent]\nprovider = "openai"\n'
    toml.write_text(original)
    with pytest.raises(ValueError):
        update(toml, "proactive", "daily_max", "nao-e-numero")
    assert toml.read_text() == original


def test_update_cria_secao_ausente(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("[agent]\nprovider = \"openai\"\n")
    update(toml, "estudos", "remind_at", "21:00")
    assert "[estudos]" in toml.read_text()
```

**Step 2: falha.**

**Step 3: implemente**

`effective()` percorre `_ENV_MAP` (env) e o TOML cru (toml) e os defaults
(pydantic `model_fields`), marcando `source`/`editable`. `update()`:

1. lê o TOML, faz `.bak`;
2. localiza/cria a seção, reescreve **só** aquela chave (`_replace_agent_key`
   generalizado, com formatação por tipo — bool minúsculo, float com `.0`,
   str entre aspas, lista `[a, b]`);
3. `tomllib.loads` + `Config.model_validate` do arquivo inteiro;
4. divergência ou exceção → restaura `.bak`, levanta `ValueError`.

**Step 4 e 5:** verde + commit `feat(control): config editavel com origem`.

---

## Tarefa 8 — `control/providers.py`

**Files:**
- Create: `gaia/control/providers.py`
- Test: `tests/test_control_providers.py`

**Step 1: teste**

```python
def test_lista_presets_com_estado_da_chave(monkeypatch, cfg_base):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    itens = {i["name"]: i for i in describe_providers(cfg_base)}
    assert itens["openai"]["has_key"] is True
    assert itens["openai"]["masked"] == "sk-…este"
    assert itens["deepseek"]["has_key"] is False
    assert itens[normalize_provider(cfg_base.agent.provider)]["current"] is True


async def test_test_provider_erro_real(monkeypatch, cfg_base):
    async def fake_list_models(self):
        raise ProviderError("Unauthorized", status_code=401)
    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", fake_list_models)
    resultado = await test_provider(cfg_base, "openai")
    assert resultado["ok"] is False
    assert resultado["status_code"] == 401
    assert "Unauthorized" in resultado["message"]
```

**Step 2 → 5:** TDD. `test_provider` constrói o provider com
`build_provider(cfg.agent.model_copy(update={"provider": name}))`, chama
`list_models()` com `asyncio.wait_for(..., 15)` e devolve
`{ok, status_code, message, models, elapsed_ms}` — erro real, nunca simulado.
Commit `feat(control): providers com teste de conexao`.

---

## Tarefa 9 — Fallback mínimo de modelo

**Files:**
- Modify: `gaia/config.py` (`AgentConfig.fallback_models`,
  `_ENV_MAP` if needed, template)
- Modify: `gaia/agent/events.py` (`ModelFallback`)
- Modify: `gaia/agent/runtime.py` (`_run_turn`)
- Test: `tests/test_agent_fallback.py`

**Step 1: teste**

```python
async def test_402_troca_para_fallback_e_emite_evento(agent_factory, cfg_base):
    cfg_base.agent.fallback_models = ["openai/gpt-4o-mini"]
    agent, chamadas = agent_factory(erros=[402, None])
    texto = await agent.prompt_sync_capture("oi")
    assert texto
    assert chamadas == ["commandcode/deepseek/deepseek-v4.1-flash",
                        "openai/gpt-4o-mini"]


async def test_500_nao_faz_fallback(agent_factory, cfg_base):
    cfg_base.agent.fallback_models = ["openai/gpt-4o-mini"]
    agent, chamadas = agent_factory(erros=[500])
    with pytest.raises(AgentError):
        await agent.prompt_sync_capture("oi")
    assert len(chamadas) == 1
```

**Step 2: falha.**

**Step 3: implemente**

`AgentConfig.fallback_models: list[str] = Field(default_factory=list)` (slugs;
`_ENV_MAP` não aceita lista, então só TOML/painel). Em `runtime.py`, extraia a
chamada ao stream para `_stream_with_fallback(request, emit)`:

```python
FALLBACK_STATUS = {401, 402, 403}

async def _stream_with_fallback(self, request, emit):
    try:
        async for delta in self._ensure_provider().stream(request):
            yield delta
    except ProviderError as exc:
        slug = self._next_fallback(exc)
        if slug is None:
            raise
        target = ModelRef.parse(slug)
        emit(ModelFallback(from_slug=..., to_slug=slug,
                           reason=str(exc), status_code=exc.status_code or 0))
        provider = build_provider(self.cfg.agent.model_copy(
            update={"provider": target.provider_id,
                    "model": target.model_id, "api_key": ""}))
        request.model = target.model_id
        async for delta in provider.stream(request):
            yield delta
```

Emitir apenas uma vez por turno (flag), nunca mudar o config. Limite de 1
alternativo (D6).

**Step 4 e 5:** `pytest tests/test_agent_fallback.py -v` + suíte + commit
`feat(agent): fallback minimo em erro de credencial`.

---

## Tarefa 10 — Instrumentação aditiva das rotinas

**Files:**
- Modify: `gaia/controller/career.py` (`_send_career`)
- Modify: `gaia/controller/briefing.py` (`_send_briefing`,
  `_send_market_briefing`, `_send_evening_report`)
- Modify: `gaia/controller/memory.py` (consolidação e sonho)
- Test: `tests/test_runs_instrumentation.py`

**Step 1: teste**

```python
async def test_career_registra_run_completed(orch_career_fake, store):
    assert await orch_career_fake._send_career(date(2026, 11, 23)) is True
    row = store.list_runs(kind="career")[0]
    assert row["status"] == "completed"
    assert row["delivery"] == "telegram"
    assert row["automation"] == "career:weekly"


async def test_career_nao_registra_run_quando_ja_entregue(orch_career_fake, store):
    store.set_meta("career:2026-11-23", "done")
    assert await orch_career_fake._send_career(date(2026, 11, 23)) is False
    assert store.list_runs(kind="career") == []
```

**Step 2: falha.**

**Step 3: implemente**

Envolver **somente o corpo após** `claim_meta` com
`async with record_run(self.store, kind=..., automation=..., delivery=...) as run:`
e preencher `run.detail` com o que a rotina já loga (nº de vagas, slot do
briefing). O gate de horário, `claim_meta`, `delete_meta` em erro e as
mensagens **não mudam**. Falha de Telegram → run `failed` com
`error_kind="network"`.

**Step 4 e 5:** rode também `test_briefing.py`, `test_career.py`,
`test_market_briefing.py` — devem passar sem alteração. Commit
`feat(controller): registra execucao real das rotinas`.

---

## Tarefa 11 — Rotas `/control/api/*`

**Files:**
- Modify: `gaia/serve/server.py` (`_Handler`, `ServeServer.__init__`)
- Modify: `gaia/serve/app.py` (`OrchestratorTarget` ganha os métodos)
- Modify: `gaia/serve/web.py` (servir `/control` e estáticos)
- Test: `tests/test_control_api.py`

**Step 1: teste**

```python
@pytest.fixture
def server(tmp_path):
    alvo = FakeControlTarget()
    ui = RemoteUI()
    srv = ServeServer(alvo, ui, host="127.0.0.1", port=0,
                      token="tok", control_token="ctl")
    url = srv.start()
    yield url
    srv.shutdown()


def test_overview_exige_control_token(server):
    assert get(server, "/control/api/overview")[0] == 401
    assert get(server, "/control/api/overview", token="tok")[0] == 401  # serve token nao serve
    status, corpo = get(server, "/control/api/overview", token="ctl")
    assert status == 200
    assert "health" in corpo


def test_pagina_control_e_publica(server):
    status, corpo = get(server, "/control")
    assert status == 200
    assert b"Control Center" in corpo


def test_sem_control_token_api_nao_existe(tmp_path):
    srv = ServeServer(FakeControlTarget(), RemoteUI(), host="127.0.0.1",
                      port=0, token="tok", control_token="")
    url = srv.start()
    try:
        assert get(url, "/control/api/overview", token="tok")[0] == 503
    finally:
        srv.shutdown()


def test_patch_settings_delega_e_devolve_origem(server):
    status, corpo = patch(server, "/control/api/settings",
                          {"section": "proactive", "key": "idle_minutes",
                           "value": 30}, token="ctl")
    assert status == 200
    assert corpo["source"] == "toml"


def test_secrets_put_mascara_na_resposta(server):
    status, corpo = put(server, "/control/api/secrets/OPENAI_API_KEY",
                        {"value": "sk-abc-1234"}, token="ctl")
    assert status == 200
    assert corpo["masked"] == "sk-…1234"
    assert "sk-abc" not in json.dumps(corpo)
```

**Step 2: falha.**

**Step 3: implemente**

`ServeServer.__init__(..., control_token: str = "")`. Autenticação separada:
novo helper `_autorizado_controle()` comparando só `control_token` (comparação
com `secrets.compare_digest`). Prefixo `/control/api/` só existe quando
`control_token` não é vazio; senão 503 com mensagem. Rotas conforme design §5.8,
cada uma delegando a `gaia/control/*` via `target`. `OrchestratorTarget` ganha
métodos finos que rodam no loop asyncio com `run_coroutine_threadsafe` e timeout,
como `models()` já faz.

**Step 4 e 5:** `pytest tests/test_control_api.py -v` + `test_serve.py`
(intacto) + commit `feat(serve): rotas do control center`.

---

## Tarefa 12 — UI estática

**Files:**
- Create: `gaia/control/static/index.html`, `style.css`, `app.js`
- Modify: `gaia/serve/web.py` (`control_page()`, `static_file(name)`)
- Test: `tests/test_control_ui.py`

**Step 1: teste**

```python
def test_staticos_existem():
    for nome in ("index.html", "style.css", "app.js"):
        assert control_static(nome) is not None


def test_pagina_nao_embute_token():
    html = control_static("index.html").decode()
    assert "Bearer " not in html
    assert "localStorage" in html
```

**Step 2: falha.**

**Step 3: implemente**

HTML com as seis abas do design §5.9, `style.css` reusando as variáveis de cor
do chat, `app.js` com `fetch` + header `Authorization`, estado de erro visível
quando faltar token, e `Cache-Control: no-cache`. Nada de framework, nada de
build. Paths de estáticos só por allowlist (nunca `..`).

**Step 4 e 5:** verde + commit `feat(control): interface do painel`.

---

## Tarefa 13 — CLI, docs e aceite

**Files:**
- Modify: `gaia/cli_serve.py` (passar `control_token`, avisar na subida)
- Modify: `gaia/serve/server.py` (checagem `remote_bind_without_token`)
- Modify: `.env.example`, `README.md`, `AGENTS.md`, `docs/README.md`
- Test: `tests/test_cli.py`, `tests/test_config.py`

**Steps**

1. `_resolver_control_token(cfg, args)`: `--control-token` >
   `GAIA_CONTROL_TOKEN` > `cfg.serve.control_token`. Sem token → o serve sobe
   normal e avisa `ui.hint("control center desativado: defina GAIA_CONTROL_TOKEN")`.
2. Novo campo `ServeConfig.control_token: str = ""` + `_ENV_MAP`
   (`GAIA_CONTROL_TOKEN`) + template TOML comentado (nunca valor) +
   `public_dict()` redigindo.
3. Docs: README com a seção "Control Center" (como definir o token, URL, abas);
   `.env.example` com `GAIA_CONTROL_TOKEN`; `AGENTS.md` com `gaia/control/` no
   mapa do código e a nova rota; `docs/README.md` movendo os dois planos.

**Aceite manual (obrigatório antes do commit final)**

```bash
.venv/bin/gaia serve --port 8788 &
# 1) /control abre e pede o token
# 2) Overview mostra modelo, saúde e últimas execuções reais
# 3) Models: testar um provider sem chave -> erro real exibido
# 4) Settings: editar proactive.idle_minutes -> conferir config.toml + .bak
# 5) Secrets: gravar OPENAI_API_KEY -> conferir .env chmod 600 e máscara
# 6) Activity: forçar uma rotina (via python -c chamando _send_career) e ver o run
```

Commit final: `docs(control): documenta control center e atualiza backlog`.

---

## Critério de pronto

- [ ] `pytest -q`, `ruff check gaia tests`, `mypy` verdes.
- [ ] `store.claim_meta` e os gates de horário unchanged (diff limpo neles).
- [ ] Nenhum valor de segredo em log, HTML, JSON ou teste.
- [ ] `/control/api/*` sem `GAIA_CONTROL_TOKEN` → 503; com token do serve → 401.
- [ ] Um run real visível em Activity após uma execução de rotina.
- [ ] `config.toml` com comentários preservados após edição pelo painel.
