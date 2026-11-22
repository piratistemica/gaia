# Prompt de continuacao — Agent Control Center, Fases C/D/E

> **Como usar:** cole o bloco "PROMPT" numa sessao nova, na raiz do repo
> (`~/gaia`). O estado abaixo foi verificado em 22/11/2026, com `838 passed`,
> `ruff` e `mypy` verdes no commit `5008564`.
> Design: `docs/plans/2026-11-22-agent-control-center-design.md`.
> Backlog detalhado: `docs/plans/2026-11-22-control-center-fases-cde.md`.

---

## PROMPT

```
Continue o Agent Control Center da Gaia (fases C, D e E). Trabalhe na branch
main, em commits pequenos, rodando os tres verificadores antes de cada commit:

    .venv/bin/python -m pytest -q
    .venv/bin/ruff check gaia tests
    .venv/bin/mypy

ANTES DE QUALQUER CODIGO

1. Leia AGENTS.md inteiro (invariantes, receitas, armadilhas, backlog).
2. Leia docs/plans/2026-11-22-agent-control-center-design.md (design aprovado).
3. Leia docs/plans/2026-11-22-control-center-fases-cde.md (este backlog).
4. Leia os tres arquivos abaixo, que sao o nucleo que voce vai mexer:
   - gaia/control/automations.py
   - gaia/controller/career.py (o padrao de record_run + claim_meta)
   - gaia/controller/briefing.py (o mesmo padrao, 3 rotinas)
5. Confirme o estado real rodando a suite. Se algo do relatorio abaixo
   divergir do codigo, O CODIGO VENCE: corrija a doc na mesma mudanca.

ESTADO ENTREGUE (Fases A e B) — NAO REFAZER, NAO REDESENHAR

Schema v6 em gaia/memory/store.py: tabela runs + begin_run, finish_run,
list_runs, last_run, runs_summary, prune_runs. SCHEMA_VERSION = 6.

gaia/control/ (logica pura, sem HTTP, sem asyncio obrigatorio):
  runs.py         record_run() (asynccontextmanager), classify_error(),
                  render_run(), recent_runs(), summary_since()
  skills.py       SKILLS (11 descritores), skill_status(), describe_skills()
  automations.py  Automation (com done_prefix e opportunistic), describe(),
                  next_run(), status(), describe_automations(), due_now(),
                  needs_attention(), MISSED_TOLERANCE_MINUTES = 20
  health.py       componente engine/database/second_brain/providers/
                  telegram/scheduler/config + overall_status()
  secrets.py      list_secrets/set_secret/remove_secret/describe_secrets,
                  allowlist, mascara (sk-...1234), backup .env.bak, chmod 600
  settings.py     effective() com origem (default|toml|env), update() com
                  rollback verificado, remove_env_override()
  providers.py    describe_providers(), test_provider() (rede real),
                  agent_settings(), credentials_summary()
  center.py       ControlCenter: junta tudo, e o que o transporte usa
  static/ (em gaia/serve/static/): control.html, control.css, control.js

Transporte: gaia/serve/control_api.py (get/post/delete) + rotas no
gaia/serve/server.py. Token separado: GAIA_CONTROL_TOKEN. Sem ele, /control
mostra instrucao e /control/api responde 503; GAIA_SERVE_TOKEN nao vale ali.

Rotas existentes (nao invente outras com o mesmo nome):
  GET  /control                       (pagina, publica)
  GET  /control/static/{asset}        (allowlist: control.html/css/js)
  GET  /control/api/overview
  GET  /control/api/health
  GET  /control/api/skills
  GET  /control/api/automations
  GET  /control/api/runs?limit&kind&status
  GET  /control/api/errors?limit
  GET  /control/api/providers
  GET  /control/api/models
  GET  /control/api/settings
  GET  /control/api/secrets
  POST /control/api/model
  POST /control/api/providers/{name}/test
  POST /control/api/settings/override
  PATCH/PUT /control/api/settings
  PUT  /control/api/secrets/{key}
  DELETE /control/api/secrets/{key}

Rotinas instrumentadas com record_run (so depois do gate de horario, com
claim_meta preservado): career (career:weekly), briefing (briefing:morning,
briefing:noon, briefing:evening), market (market:watchlist), memory
(memory:daily), dream (memory:dream).

Fallback de modelo: agent.fallback_models (lista de "provider/model");
gaia/agent/runtime.py::_stream_with_fallback troca UMA vez em 401/402/403 e
emite ModelFallback. NAO faz fallback em 429/5xx (decisao D6).

Testes ja existentes (leia antes de escrever novos):
  tests/test_control_runs.py, test_control_skills.py,
  test_control_automations.py, test_control_health.py,
  test_control_secrets.py, test_control_settings.py,
  test_control_providers.py, test_control_api.py,
  test_agent_fallback.py, test_runs_instrumentation.py

O QUE O PAINEL AINDA NAO FAZ (e o que voce vai fazer)

O painel OBSERVA; nao GOVERNA. Os quatro workers do Orchestrator
(_proactive_loop, _briefing_loop, _career_loop, _memory_loop) continuam
decidindo sozinhos a hora com if <due>(...). automations.py duplica esse
conhecimento apenas para descrever a tela — e essa duplicacao e a divida que
a Fase C precisa pagar.

REGRA QUE MANDA EM TUDO: um unico processo escritor. Nada de APScheduler, nada
de cron do sistema, nada de segundo SQLite. O agendador e o Orchestrator.

FASE C — scheduler governavel (a parte mais delicada)

C.1 Schema v7: tabela automations (id TEXT PK, skill, enabled, days, at,
    until, delivery, retry_max, retry_backoff_minutes, last_result,
    updated_at). Migracao v6->v7 idempotente em _init_schema/_migrate.
    SEED na primeira migracao a partir do Config, com os mesmos valores de
    hoje (career.days/at/until, briefing.*, market_days/at/until, memory.at,
    dream_at/until). Depois do seed, o comportamento observado NAO pode mudar:
    e a rede de seguranca de toda a fase.

C.2 Um handler por vez, nesta ordem (a mais barata primeiro):
      1. market:watchlist   (test_market_briefing.py)
      2. briefing:morning/noon/evening (test_briefing.py)
      3. career:weekly      (test_career.py)
      4. memory:daily / memory:dream (test_memory_consolidation.py, test_dream.py)
    Para cada um: escreva o teste do scheduler, converta o gate, rode TAMBEM
    os testes antigos daquele dominio, e so entao siga. Se os antigos ficarem
    vermelhos, pare e conserte — nao siga adiante "para arrumar depois".

C.3 _scheduler_loop no Orchestrator (substitui os gates, nao os workers):
    a cada N segundos le as automations habilitadas, calcula due() e chama o
    handler registrado. Mantenha claim_meta como SEGUNDA barreira, agora
    chaveada por automation_id + janela. A tabela automations e o painel sao a
    primeira; claim_meta continua impedindo entrega dupla se algo escapar.

C.4 Endpoints novos:
      PATCH /control/api/automations/{id}      {enabled, days, at, until, ...}
      POST  /control/api/automations/{id}/run      (trigger="manual")
      POST  /control/api/automations/{id}/pause
      POST  /control/api/automations/{id}/resume
      GET   /control/api/automations/{id}/runs
    `run` devolve 409 se o motor estiver ocupado. Toda escrita valida o
    payload no modelo da automation (nao aceite "09:99").

C.5 Retry: so para error_kind em {network, unknown}, com backoff
    configuravel e teto retry_max; incrementa attempt. auth e config NAO
    fazem retry: o problema e humano e insistir esconde o erro.

C.6 CRUD de skills no painel: ativar/desativar grava no Config (nao no
    codigo), "testar" chama o handler com dry_run=True, e mostra o historico.
    Skill nova continua entrando so pelo registry em gaia/control/skills.py.

C.7 Notificacao de falha (decisao D8 ja aprovada): falha de automacao com
    delivery="telegram" manda UMA mensagem curta no chat (uma por falha, nao
    por tentativa, usando claim_meta para nao repetir apos restart). Falha de
    automacao local vai so para o Error Center.

C.8 UI: botoes de pausar/retomar/executar e edicao de horario em
    gaia/serve/static/control.js (aba Automations). Mantenha o padrao atual:
    sem framework, sem build, fetch com Authorization, erro visivel na tela.

FASE D — proatividade e autonomia

D.1 Config nova [proactivity] com nivel por skill:
    autonomy = "observe" | "inform" | "act"  (padrao inform)
    + require_confirmation para acoes sensiveis.
    Adicione no modelo pydantic, em _ENV_MAP (se fizer sentido), no
    _DEFAULT_CONFIG_TEMPLATE e no .env.example.

D.2 Roteamento no Orchestrator:
    observe -> registra run/deteccao, nao fala com a usuaria
    inform  -> _say_proactive (caminho atual)
    act     -> executa, mas passa pelo sistema de permissao existente
               (agent/permissions) quando a acao for externa/sensivel

D.3 Aplicar a correcao de docs/plans/2026-11-22-proatividade-e-ambiente.md:
    no `gaia serve` a proatividade cai em _speak() (TTS local numa maquina
    headless) em vez do Telegram. Correcao: flag owns_telegram +
    _say_proactive entregando no chat + _speak() nao falando em serve.
    Os dois testes estao escritos no plano (secao 4, fase 0.1) — use-os.

D.4 Sugestao de automacao: quando o agente detectar tarefa recorrente,
    PROPOR criar automacao (nivel inform). Nunca criar sozinho.

FASE E — fechamento

E.1 Retencao de runs (90 dias) com prune_runs num job diario.
E.2 Exportacao de diagnostico (JSON) pelo Error Center.
E.3 Testes de integracao ponta a ponta: serve em porta efemera, token,
    execucao manual, falha simulada, retry, notificacao.
E.4 Atualizar README.md (Control Center), AGENTS.md (mapa + backlog) e
    docs/README.md (status dos planos) na mesma mudanca.

REGRAS QUE NAO MUDAM

- Zero dependencia nova (stdlib + httpx + pydantic).
- Um processo escritor; um SQLite; segredos so no .env.
- Toda rota /control/api/* exige GAIA_CONTROL_TOKEN.
- Nenhum valor de segredo em JSON, HTML, log ou teste.
- UI, mensagens e testes em portugues; comentarios em ASCII sem acento.
- Se a doc divergir do codigo, corrija a doc na mesma mudanca.

COMO PROVAR QUE FUNCIONOU

Fase C so esta pronta quando:
  - a suite inteira passa SEM alterar test_briefing.py, test_career.py,
    test_market_briefing.py (eles sao a prova de que o comportamento nao mudou);
  - pausar uma automacao no painel impede a execucao de verdade (verifique no
    log e em runs, nao so na tela);
  - executar manualmente gera run com trigger="manual";
  - uma falha de rede e tentada de novo com backoff e a de auth nao.
```

---

## Checklist de retomada

- [ ] `git log --oneline -1` mostra `5008564` ou posterior.
- [ ] `838 passed` (ou mais, se voce ja adicionou testes).
- [ ] `sqlite3 ~/.local/share/gaia/gaia.db ".schema runs"` existe.
- [ ] `gaia serve` sobe e `/control` abre com `GAIA_CONTROL_TOKEN` no `.env`.
- [ ] `docs/README.md` marca A/B como implementado e C/D/E como backlog.

## O que eu faria diferente, se recomecasse

1. **`automations.py` descreve a agenda em paralelo aos workers.** Foi a
   escolha certa para entregar A/B sem risco, mas e duplicacao real: se
   `career_due` mudar, o painel diverge em silencio. A C.2 existe para pagar
   essa divida — nao a adie.
2. **`begin_run(started_at=...)` nasceu para testar execucao de ontem.** Nao e
   hack (serve para importar historico), mas foi conveniencia de teste virando
   API. Se for usar de verdade, documente o caso de uso.
3. **Nada do painel foi visto num browser.** Sintaxe JS validada com
   `node --check`, rotas testadas com payload real, layout nao. Ao encostar no
   `control.js` na Fase C, aproveite para abrir e olhar.
