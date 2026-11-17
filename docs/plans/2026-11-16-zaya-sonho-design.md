# Design — Sonho noturno da Zaya

**Data:** 2026-11-16
**Status:** implementado (`zaya/memory/dream.py`)

## Problema

A consolidação diária responde "o que aconteceu hoje". O que falta é a passagem
seguinte: o dia consolidado voltar a encontrar o que a Zaya já viveu, e disso
sair algo novo. Sem isso, o Second Brain cresce como arquivo — acumula notas que
só reaparecem quando a busca acha uma palavra-chave exata.

## Ideia

Quando estiver ociosa de madrugada, a Zaya **sonha**: lê a memória do dia,
procura no índice as memórias antigas que ressoam e escreve uma nota de sonho no
Second Brain. O sonho é o único lugar do sistema onde a Zaya produz
interpretação em cima da própria memória, e não registro.

## Decisões

| Decisão | Escolha | Alternativa descartada |
|---|---|---|
| Insumo | nota consolidada do dia + busca no índice | turnos crus do dia (duplicaria a consolidação) |
| Horário | janela `dream_at`–`dream_until` (`02:00`–`08:00`), depois da consolidação | junto da consolidação (sonharia sobre dia não escrito) |
| Dia sonhado | último dia fechado: ontem de madrugada, hoje à noite | `now.date()` cru (sonharia sobre um dia vazio às 3h) |
| Frequência | 1x/dia via `meta['memory_dreamed:<dia>']` | a cada ciclo ocioso (repetitivo e caro) |
| Entrega | silencioso, só nota no Second Brain | mandar no Telegram (disputa com quiet hours) |
| Pasta | `Sonhos/` (decisão da Talita): interpretação não se mistura com registro | mesma pasta da memória (poluía a busca) |
| Aviso | uma linha no dia seguinte, às 08:00, sem LLM | silêncio total (ninguém sabia que houve sonho) |
| Grafo | `links=["Memória <dia>"]` na criação | nota órfã (`links: []`) |
| Seleção | `keywords()` sem stopwords + `_PER_NOTE = 2` | busca crua (uma nota ocupava 3 vagas) |
| Catch-up | `_dream_catch_up` na subida, só de manhã | perder o dia se a máquina estava off às 2h |
| Falha do modelo | nota determinística com dia + antigas | abortar (perderia a noite) |
| Gatilho | `_memory_loop`, respeitando `_busy`/`_recording`/`_listening` | `_proactive_loop` (teria que fingir ser iniciativa) |

## Fluxo

```text
_memory_loop (60s)
  └─ _maybe_dream(now)
       ├─ enabled? na janela noturna?  não → volta
       ├─ dia alvo = ontem (madrugada) ou hoje (noite)
       ├─ consolidate_day(dia alvo)    garante a nota do dia
       └─ Dreamer.dream(hoje)
            ├─ meta['memory_dreamed:<dia>'] existe?  sim → volta
            ├─ lê Memoria/Memória <dia>.md
            ├─ index.search(texto do dia) + index.recent()  → 5 memórias antigas
            ├─ prompt de sonho (LLM, com timeout)
            ├─ compose_dream_note (fallback determinístico)
            └─ Memoria/Sonho <dia>.md + index + records(kind='dream')
```

## Nota gerada

```markdown
---
title: Sonho 2026-11-16
tags: [sonho, memoria, 2026-11-16]
---

<parágrafo onírico>

## Ecos do dia
## Ressonâncias
## Pergunta para amanhã

## Memórias revisitadas
- **Diário de campo** (`Inbox/Diário de campo.md`): ...

## Relações
- [[Memória 2026-11-16]]
```

## Configuração

```toml
[memory]
dream_enabled = true
dream_at = "02:00"
dream_until = "08:00"
dream_folder = "Sonhos"
dream_notify = true
dream_notify_at = "08:00"
```

Variáveis: `ZAYA_MEMORY_DREAM_ENABLED`, `ZAYA_MEMORY_DREAM_AT`,
`ZAYA_MEMORY_DREAM_UNTIL`, `ZAYA_MEMORY_DREAM_FOLDER`,
`ZAYA_MEMORY_DREAM_NOTIFY`, `ZAYA_MEMORY_DREAM_NOTIFY_AT`.

### Janela e dia sonhado

Comparar `"02:00" < now` com string dava sonho às 9h da manhã. Por isso a janela
é explícita e pode virar a meia-noite: `[dream_at, dream_until)`. E o dia
sonhado é o último dia **fechado** — de madrugada, ontem; depois do meio-dia,
hoje. Sem isso, a checagem das 3h consolidaria e sonharia sobre um dia que só
tem três horas de vida.

Ficar fora da janela não perde o sonho: a guarda é `meta['memory_dreamed:<dia>']`,
então a madrugada seguinte ainda sonha sobre o mesmo dia, com `force` implícito
não — se já sonhou, não repete.

## Aviso no dia seguinte

`_maybe_dream_report` (no `_memory_loop`) manda uma linha às `dream_notify_at`:

```text
Sonhei com 15/11: Um exemplo qualquer de abertura. Ficou a pergunta: Exemplo de pergunta?
```

Regras: respeita `proactive.quiet_hours`, reserva `meta` antes de falar (TUI e
Telegram coexistem), `count=False` para não comer o `daily_max` da
proatividade, e o texto sai da própria nota (`dream_recap`) — sem segunda
chamada ao modelo. Sem nota ou sem frase/pergunta, não avisa.

## Comando

`/sonhar [hoje|ontem|AAAA-MM-DD] [forcar]` — consolida o dia se preciso e sonha
na hora. Mesmo parser de `/consolidar` (`parse_day`).

## Testes

`tests/test_dream.py` — uma vez por dia, sonho pelo modelo, fallback, só
memórias antigas, nada para sonhar, desabilitado, composição da nota, janela
noturna (inclusive virando a meia-noite), horário inválido e dia alvo.

`tests/test_orchestrator.py` — `/sonhar ontem forcar` consolida e sonha o dia
pedido, sonho só dentro da janela, madrugada sonha ontem, noite sonha hoje,
`dream_enabled=false` não sonha.
