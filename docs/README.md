# Documentação — índice e status

Fonte de verdade para agentes: [`../AGENTS.md`](../AGENTS.md). Manual do usuário:
[`../README.md`](../README.md). Regra geral: **se a doc divergir do código, o
código vence** — corrija a doc na mesma mudança.

## Referência viva (confie)

| Arquivo | O que é |
|---|---|
| [`../README.md`](../README.md) | manual do usuário: instalação, uso, CLI, modos, config, privacidade |
| [`../AGENTS.md`](../AGENTS.md) | manual do agente: comandos, mapa do código, invariantes, receitas, armadilhas, backlog |
| [`../.env.example`](../.env.example) | todas as variáveis `GAIA_*`, comentadas |
| [`acesso-mini.md`](acesso-mini.md) | operação do mini PC: SSH, `gmin`, Tailscale |

## Planos e design datados

Documentos de sessão. Descrevem decisões da época; **não** são referência de
estado atual. O status abaixo foi verificado no código.

| Arquivo | Status |
|---|---|
| [`plans/2026-11-16-gaia-linux-telegram-design.md`](plans/2026-11-16-gaia-linux-telegram-design.md) | implementado (serve/Telegram evoluíram depois) |
| [`plans/2026-11-16-gaia-linux-telegram.md`](plans/2026-11-16-gaia-linux-telegram.md) | implementado |
| [`plans/2026-11-16-gaia-sonho-design.md`](plans/2026-11-16-gaia-sonho-design.md) | implementado (`controller/memory.py`, `memory/dream.py`) |
| [`plans/2026-11-16-gmin-admin.md`](plans/2026-11-16-gmin-admin.md) | parcial: `gmin.sh` tem doctor/logs/restart/deploy/test/agenda/ask/shell/sudo/serve; backup/restore/audit/fix não existem |
| [`plans/2026-11-19-serve-cliente-fino.md`](plans/2026-11-19-serve-cliente-fino.md) | fases 1 e 2 implementadas (`gaia serve`, `--remote`, web); partes 2/3 são backlog |
| [`plans/2026-11-20-handoff.md`](plans/2026-11-20-handoff.md) | diário de sessão; leia o "Estado operacional", mas confirme no ambiente |
| [`plans/2026-11-22-proatividade-e-ambiente.md`](plans/2026-11-22-proatividade-e-ambiente.md) | **plano ativo, não aplicado**: no `serve` a proatividade vai para o TTS local em vez do Telegram; `~/.zshrc` aponta para config sem `.env` |
| [`plans/2026-11-22-mac-cliente-fino.md`](plans/2026-11-22-mac-cliente-fino.md) | **plano ativo, não aplicado**: o que falta para o Mac virar cliente fino (TUI remota, `ui.confirm`, 12 eventos não desenhados, voz, trava de escrita). Inclui o dano da sobrescrita silenciosa do Second Brain |
| [`plans/2026-11-22-agent-control-center-design.md`](plans/2026-11-22-agent-control-center-design.md) | **design aprovado, não implementado**: Control Center web no `gaia serve` (providers, secrets mascarados, config com origem, histórico de execuções, system health) |
| [`plans/2026-11-22-control-center-ab.md`](plans/2026-11-22-control-center-ab.md) | **plano ativo, não aplicado**: Fases A e B do Control Center, tarefa por tarefa com testes |
| [`plans/2026-11-22-control-center-fases-cde.md`](plans/2026-11-22-control-center-fases-cde.md) | **backlog, não aplicado**: Fases C/D/E (scheduler governável, autonomia, retry, notificação de falha) com prompt de retomada |

## Specs e análises

| Arquivo | Status |
|---|---|
| [`prompt-correcoes.md`](prompt-correcoes.md) | **backlog ativo, não aplicado**: lembretes perdidos, `always` por sessão, truncar o fim do bash |
| [`memoria-plano.md`](memoria-plano.md) | parcial: consolidação e sonho implementados; schema v6 (`facts`, `recall`) não |
| [`memoria-checkin-18h.md`](memoria-checkin-18h.md) | proposta **não implementada** (não há revisão de memória às 18h) |
| [`limpeza-plano.md`](limpeza-plano.md) | parcial/histórico: Spotify já não existe; linhas citadas podem não bater |
| [`comparacao-agentes-chat.md`](comparacao-agentes-chat.md) | análise datada (16/11/2026), não é especificação |

## Histórico puro (não use como referência)

| Arquivo | Por quê |
|---|---|
| [`readme-antigo.md`](readme-antigo.md) | README de uma versão anterior; mantido só como registro |

## Onde procurar cada coisa

- Comportamento de CLI e comandos: `README.md` (uso) e `cli_parser.py` (verdade).
- Config e variáveis: `gaia/config.py` (`_ENV_MAP`, `_DEFAULT_CONFIG_TEMPLATE`) e
  `.env.example`.
- Regras do agente, segurança e invariantes: `AGENTS.md`.
- Estado operacional do mini: `acesso-mini.md` e `plans/2026-11-20-handoff.md`.
