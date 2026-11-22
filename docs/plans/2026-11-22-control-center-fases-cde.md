# Agent Control Center — Fases C, D e E (prompt para retomar depois)

> **Status: em execução (fase C parcial).** Documento de retomada. O design
> completo está em `2026-11-22-agent-control-center-design.md`; as Fases A e B
> saíram na sessão anterior. Leia o design antes deste arquivo. O estado
> verificado de cada item está na seção seguinte, no fim do arquivo.

## Estado da execução (verificado no código)

Atualize esta tabela **na mesma mudança** em que o item sair; ela é a única
lista de status deste plano.

| Item | Status | Onde / observação |
|---|---|---|
| C.1 tabela `automations` (schema v7) | **feito** (`39142d0`) | `MemoryStore` (`upsert_automation`, `update_automation`, `list_automations`, `get_automation`) + `control/automations.seed/load/update` |
| C.2 endpoints das automações | pendente | `GET /automations` já existe (fase A); faltam `PATCH {id}`, `run`, `pause`, `resume`, `runs` |
| C.3 scheduler único | **feito** (`2ccbe99`) | `gaia/controller/scheduler.py` (`_scheduler_loop`, `_scheduler_tick`, `run_automation_now`); gates de hora removidos de briefing/career/memory. `claim_meta` preservado como segunda barreira |
| C.4 missed + retry | **feito** (`2ccbe99`) | `retry_allowed`/`backoff_seconds`; só `network`/`unknown`; `auth`/`config` não repetem |
| C.5 CRUD de skills no painel | pendente | ativar/desativar grava no TOML via `control/settings.update`; `dry_run` não existe nos handlers |
| C.6 notificação de falha (D8) | **feito** (`2ccbe99`) | `_notify_automation_failure` com `claim_meta("automation_failed:<id>:<dia>")`, uma por falha |
| C.7 (report) | — | a numeração do prompt de retomada difere deste arquivo: aqui C.2 são os endpoints e C.5/C.6 são skills/notificação |
| D.1 `[proactivity] autonomy` | pendente | modelo, `_ENV_MAP`, `_DEFAULT_CONFIG_TEMPLATE`, `.env.example` |
| D.2 roteamento observe/inform/act | pendente | hoje `_say_proactive` sempre fala |
| D.3 proatividade no `serve` | **feito** (`12f15b1`) | `owns_telegram` + `_speak` não toca áudio em `serve`; testes do plano de proatividade aplicados |
| D.4 sugestão de automação | pendente | nunca cria sozinho |
| E.1 retenção de runs | pendente | `prune_runs` já existe; falta o job diário |
| E.2 export de diagnóstico | pendente | rota do Error Center |
| E.3 integração ponta a ponta | pendente | porta efêmera, token, run manual, falha, retry, notificação |
| E.4 README/AGENTS/.env.example | pendente | atualizar na mesma mudança dos itens acima |
| E.5 `docs/README.md` | pendente | move este plano para "implementado" no fim |

**Divergência conhecida entre este arquivo e o prompt de retomada:** o prompt
lista os endpoints como "C.4"; este arquivo os chama de C.2. O código segue os
nomes das rotas, não a numeração. Ao concluir a fase, unifique.

## Como usar este documento

Copie a seção "Prompt" abaixo numa sessão nova. Ela assume que as Fases A e B
já estão no `main` e verdes no CI.

---

## Prompt

```
Continue o Agent Control Center da Gaia. Antes de qualquer código:

1. Leia AGENTS.md (invariantes, armadilhas, status e backlog).
2. Leia docs/plans/2026-11-22-agent-control-center-design.md (design aprovado).
3. Confirme no código que as Fases A e B estão implementadas:
   gaia/control/, gaia/serve/server.py com /control/api/*, schema v6 em
   gaia/memory/store.py, config.agent.fallback_models, estáticos em
   gaia/control/static/.
4. Rode a suíte antes de mexer: .venv/bin/python -m pytest -q && ruff && mypy.

Não reescreva a arquitetura de A/B. Este trabalho é uma evolução aditiva.
A regra do repo continua: um único processo escritor, um único SQLite,
segredos só no .env.

## Contexto do que existe

Fase A entregou:
- gaia/control/ (runs, skills, automations, health, secrets, settings, providers)
- schema v6: tabela runs + métodos no MemoryStore
- instrumentação ADITIVA das rotinas (career, briefing, market, evening,
  memory, dream) com record_run(); o agendador NÃO mudou.
- fallback mínimo de modelo (401/402/403) com evento ModelFallback.
- GAIA_CONTROL_TOKEN obrigatório para /control/api/*.

Consequência a corrigir nesta fase: os loops continuam decidindo sozinhos a
hora (if career_due(...)), então o painel consegue OBSERVAR mas não GOVERNAR.
O claim_meta em cada rotina é o que impede entrega dupla — não o remova antes
de existir substituto testado.

## Fase C — Skills como cidadãs de primeira classe + automações governáveis

Objetivo: o painel pausa, retoma e executa uma automação na hora, e o
agendador passa a obedecer uma agenda declarada em vez de ifs hardcoded.

C.1 Tabela automations (schema v7):
    id, skill, enabled, days, at, until, delivery, retry_max,
    retry_backoff_minutes, last_result, updated_at.
    Seed na primeira migração a partir do Config atual (career.days/at/until,
    briefing.*, market_days/at/until, memory.at, dream_at), de forma que o
    comportamento observado hoje NÃO mude.

C.2 Endpoints:
    PATCH /control/api/automations/{id}   {enabled, days, at, until, ...}
    POST  /control/api/automations/{id}/run      (execução manual, trigger=manual)
    POST  /control/api/automations/{id}/pause
    POST  /control/api/automations/{id}/resume
    GET   /control/api/automations/{id}/runs

C.3 Scheduler único (substitui os gates por rotina):
    um _scheduler_loop no Orchestrator que, a cada N segundos:
      - lê automations habilitadas;
      - calcula due(cfg, automation, now) usando a MESMA lógica de
        due_slot/market_due/evening_due/career_due (reaproveitar as funções
        puras de gaia/briefing/compose.py e gaia/career/service.py);
      - chama o handler registrado da skill;
      - mantém claim_meta como segunda barreira (uma vez por janela), agora
        chaveado por automation_id + janela.
    Ordem de implementação OBRIGATÓRIA: um handler por vez, com os testes
    existentes (test_briefing.py, test_career.py, test_market_briefing.py)
    rodando a cada passo. Os loops antigos saem só depois que o handler
    equivalente estiver verde.

C.4 Detecção de "missed" baseada em runs + janela, e retry:
    - retry só para error_kind em {network, unknown}, com backoff configurável
      e teto (retry_max); auth/config não fazem retry (o problema é humano).
    - durante retry, status = running com attempt incrementado.

C.5 CRUD de skills no painel: ativar/desativar (escreve no Config, não no
    código), testar (chama o handler com dry_run=True), ver histórico.
    Skills novas entram no registry sem tocar o painel.

C.6 Notificação de falha no Telegram (decisão D8 já aprovada):
    falha de automação com delivery=telegram envia mensagem curta ao chat;
    falha de automação local vai só para o Error Center.
    Uma notificação por falha, não por tentativa. Usar claim_meta para não
    repetir após restart.

## Fase D — Proatividade e autonomia

D.1 Config nova: [proactivity] com níveis por skill:
    autonomy = "observe" | "inform" | "act"   (padrão inform)
    e require_confirmation para ações sensíveis.

D.2 Roteamento no Orchestrator:
    observe -> registra run/detecção, não fala com a usuária;
    inform  -> _say_proactive (caminho atual);
    act     -> executa a ação, mas passa pelo sistema de permissão existente
               (agent/permissions) quando a ação for externa/sensível.

D.3 Corrigir a armadilha documentada em AGENTS.md: no `gaia serve` a
    proatividade cai em _speak() (TTS local) em vez do Telegram. Isso está
    descrito em docs/plans/2026-11-22-proatividade-e-ambiente.md; aplicar a
    correção de _say_proactive/owns_telegram junto desta fase, com os dois
    testes do plano.

D.4 Sugestão de automação: quando o agente detectar tarefa recorrente
    (ex.: pedido repetido de vagas), propor criar automação — como recomendação
    (inform), nunca criando sozinho.

## Fase E — Fechamento

E.1 Retenção de runs (ex.: 90 dias) com job diário.
E.2 Exportação de diagnóstico (JSON/zip) a partir do Error Center.
E.3 Testes de integração ponta a ponta: serve real em porta efêmera, token,
    execução manual de automação, falha simulada, retry, notificação.
E.4 Atualizar README.md (seção do Control Center), .env.example
    (GAIA_CONTROL_TOKEN) e AGENTS.md (mapa do código: gaia/control/).
E.5 docs/README.md: mover este plano de "ativos" para "implementado".

## Regras que NÃO mudam

- Nenhuma dependência nova de terceiros (stdlib + httpx + pydantic).
- Um processo escritor; nada de cron do sistema, nada de APScheduler.
- Segredos só no .env; o painel só mascara e escreve, nunca loga valor.
- Toda rota /control/api/* exige GAIA_CONTROL_TOKEN.
- Se a doc divergir do código, corrija a doc na mesma mudança.
- Testes em português; comentários ASCII sem acento.
```

---

## Checklist de retomada (para quem for executar)

- [ ] `git log` mostra o commit das Fases A/B e o CI verde.
- [ ] `gaia serve` sobe com `GAIA_CONTROL_TOKEN` e o painel abre.
- [ ] `sqlite3 gaia.db ".schema runs"` existe (schema v6+).
- [ ] Existe ao menos um run real registrado por `career`/`briefing`.
- [ ] `docs/README.md` lista o design e este arquivo.
