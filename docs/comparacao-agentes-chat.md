# Zaya × agentes.chat — comparação

Repo comparado: `github.com/agentes-chat/agentes-chat` (clone em `/tmp/agentes-chat`,
commit `c4cdbf1`, 16/11/2026).
Zaya: `~/zaya` (commit `0c04ee1` + WIP local).

**Resumo em uma linha:** não são concorrentes — o agentes.chat é um **Slack com
agentes dentro** (multi-tenant, servidor, web/desktop/mobile); a Zaya é uma
**agente pessoal que mora no seu Mac** (voz, local-first, uma pessoa). O que os
une é a mesma tese: agente com papel, memória, rotina e aprovação — não um chatbot.

---

## 1. Escala e stack

| | Zaya | agentes.chat |
|---|---|---|
| Linguagem | Python 3.12 | TypeScript (Bun) |
| Código de app | ~18.900 linhas de Python | ~1,1 milhão de linhas TS/TSX (4.401 arquivos) |
| Estrutura | pacote único `zaya/` (17 módulos) | monorepo: 4 apps + 11 packages |
| Persistência | SQLite (`zaya.db`) + Markdown | Postgres + pgvector, Drizzle, outbox |
| Interface | TUI Textual + terminal Rich | web (React/Vite/TanStack), macOS/Win/Linux (Tauri), iPhone/Android (Expo) |
| Backend | nenhum — fala direto com a API do modelo | Hono + Zod OpenAPI, better-auth, multi-tenant |
| Deploy | `uv tool install`, roda na máquina | Docker compose, `curl ... | sh`, Tailscale/Caddy |
| Licença | **MIT** | **AGPL-3.0** (+ licença comercial p/ rebrand) |
| Testes | 34 arquivos pytest, cobertura em `.coverage` | `bun test` + pglite, evals por comportamento |

Diferença de ordem de grandeza: ~1:60 em linhas. Isso define tudo o resto.

---

## 2. O que cada uma é, de verdade

**Zaya** — uma agente com nome, voz e iniciativa para *uma* pessoa. O eixo é
presença: ela ouve (STT local, áudio nunca sai da máquina), responde falando,
dá bom dia, avisa lembrete, consolida o dia às 23h. Tudo local, chave própria,
zero servidor. Quatro personas (pesquisadora/trabalhadora/investidora/assistente)
que trocam o prompt, não a infraestrutura.

**agentes.chat** — infraestrutura de time. Agentes são **membros do workspace**
com dono, papel, orçamento mensal e escopo de conectores. O produto é a
conversa compartilhada: canal, thread, DM, aprovação como card no chat. O eixo é
governança: quem pode o quê, quanto custou, o que espera aval humano.

---

## 3. Feature a feature

| Capacidade | Zaya | agentes.chat | Leitura |
|---|---|---|---|
| Loop de agente com ferramentas | ✅ 7 tools (`read/write/edit/list/glob/grep/bash`) | ✅ gateway de tools + MCP | Zaya é mínima e suficiente; o outro é extensível |
| Voz de entrada | ✅ **faster-whisper local + VAD Silero** | ⚠️ STT só de áudio gravado (adapters/speech, provedores pagos) | **Zaya ganha forte** — local, streaming, hands-free |
| Voz de saída (TTS) | ✅ edge-tts + `say`, limpeza de Markdown | ❌ | **Zaya ganha** |
| Memória | ✅ Second Brain Markdown + FTS5 incremental + consolidação diária com fallback determinístico | ✅ memória em Postgres, "judgments" por modelo pequeno (Jev), propostas de mudança de instrução | Filosofias opostas: arquivo seu vs. banco gerenciado |
| Memória como arquivo legível | ✅ Markdown + `[[links]]`, backup e trash próprios | ✅ Wiki com histórico, backlinks, grafo, import Obsidian | Empate — resolveram o mesmo problema dos dois jeitos |
| Iniciativa / proatividade | ✅ check-in, briefing 8h/12h, fecho do dia, rotina de vagas | ⚠️ routines por schedule/evento, sem "presença" | Zaya tem *ritmo*; o outro tem *cron* |
| Aprovação humana | ✅ confirmação em write/edit/bash, auto-approve opt-in | ✅ approvals com dono, admin, ação executa uma vez | Equivalente em espírito; o outro é auditável |
| Orçamento por agente | ⚠️ contagem de tokens/uso, sem teto por agente | ✅ reserva antes de cada chamada, alerta ao dono | **agentes.chat ganha** |
| Multi-provider | ✅ deepseek/openai/anthropic/openrouter/ollama/custom + `/modelo` ao vivo | ✅ Claude, OpenAI, Gemini, DeepSeek, Kimi, Z.ai, MiniMax, OpenRouter | Empate |
| Conectores externos | ⚠️ Spotify, Agenda, Telegram, WhatsApp, Playwright | ✅ MCP + Composio (40+ apps) | **agentes.chat ganha** |
| Time / multiusuário | ❌ uma pessoa | ✅ workspaces, canais, DMs, threads | Não é o problema da Zaya |
| Computador do agente | ✅ é o próprio Mac (bash local) | ✅ runner isolado, VM, sandbox de código | Zaya é mais direta; o outro é mais seguro |
| Navegador | ✅ Playwright com perfil persistente | ✅ `agent-browser`, assistível ao vivo, handover | Empate |
| Skills | ⚠️ personas + playbook em Markdown lido pela agente | ✅ `SKILL.md` versionado, zip/GitHub/editor, escopo por agente | **agentes.chat ganha** (versionamento e escopo) |
| Tarefas | ✅ `tasks/` + Agenda | ✅ board com colunas, agente move sozinho | Equivalente |
| Agenda / rotina | ✅ módulo Agenda + lembretes | ⚠️ routines + Calendar via Composio | Empate |
| Finanças | ✅ carteira, watchlist, briefing de mercado seg/qua/sex | ❌ | **Zaya ganha** |
| Carreira | ✅ rotina de vagas com score, playbook, CV LaTeX, entrega garantida | ❌ | **Zaya ganha** (é domínio, não infra) |
| Observabilidade | ⚠️ logs em `data/logs` | ✅ audit, autonomy report, eval suites (memória, router, turnos, browser, mídia) | **agentes.chat ganha** |
| i18n | ❌ pt-BR fixo no prompt | ✅ 14 idiomas | **agentes.chat ganha** |
| Interop | ✅ exporta bundle `agenteschat.bundle` (`agentes-chat-export.json`) | ✅ importa de Grok Bot, ChatGPT dots, OpenBot, OpenClaw, etc. | Já conversam |

---

## 4. Onde a Zaya é melhor (e vale proteger)

1. **Voz local de verdade.** STT incremental com VAD, push-to-talk, TTS no ritmo
   da conversa. O agentes.chat só transcreve áudio gravado, por provedor pago.
   Isso é vantagem estrutural, não feature.
2. **Local-first radical.** Áudio nunca sai; memória é pasta de Markdown sua;
   roda sem servidor, sem Postgres, sem Docker. O outro exige um Linux, Postgres
   e 10 minutos de setup.
3. **Presença e ritmo.** Bom dia, check-in com limite diário e horário de
   silêncio, fecho do dia com fallback determinístico ("o dia nunca se perde").
   O agentes.chat agenda rotinas; a Zaya *habita* o dia.
4. **Domínio próprio.** Carreira (playbook → score → CV LaTeX → entrega no
   Telegram com reenvio garantido) e finanças são produto, não configuração.
   Nada disso existe lá.
5. **Uma superfície de config.** Um `config.toml` + `.env`, com `zaya doctor`.
   O outro tem 46 KB de `.env.example`.
6. **Custo cognitivo.** 19k linhas que uma pessoa entende inteiras.

---

## 5. Onde o agentes.chat é melhor (e o que copiar)

Em ordem de retorno sobre esforço para a Zaya:

1. **Aprovação com escopo e dono** (ADR 0015, tool gateway). A Zaya pergunta
   caso a caso; eles modelam "a ação aprovada roda uma vez, exatamente como
   aprovada", com cobertura por conector, dono e admin. Copiar o conceito:
   aprovação com prazo, escopo e registro — não um `y/n` que expira no terminal.
2. **Orçamento antes da chamada** (ADR 0012). Reserva, checa teto, registra uso
   por agente. A Zaya só conta tokens *depois*. Um teto mensal por modo/persona
   evitaria surpresa no DeepSeek.
3. **Skills versionadas em `SKILL.md`** (ADR 0020). O playbook de vagas é
   ótimo, mas mora em `~/SecondBrain` e é lido inteiro. Skills com versão,
   descrição e carregamento sob demanda reduziriam o prompt e dariam histórico.
4. **Evals por comportamento** (ADR 0003 + `packages/engine/eval`). Eles testam
   memória, router, turnos, menções, resiliência com suites próprias. A Zaya tem
   34 testes unitários e nada que meça "a agente respondeu bem?".
5. **Router de menções** (ADR 0013). Com quatro personas, a Zaya decide o modo
   por comando explícito. Um classificador pequeno (ou regra) que propõe o modo
   e diz por que escolheu seria natural — e barato.
6. **Isolamento de execução** (ADR 0030). Hoje `bash` roda no Mac da Talita com
   `allow_outside_project=true` por padrão. Um runner com diretório de trabalho
   restrito por sessão, e a opção de negar rede, fecharia o maior risco aberto.
7. **Wiki com histórico + backlinks + grafo** (ADR 0023). O Second Brain tem
   links `[[ ]]` e FTS5, mas não tem histórico por nota nem backlinks
   navegáveis. É a evolução natural de `secondbrain/`.
8. **Import/export como contrato.** O `agenteschat.bundle` já existe. Manter o
   export atualizado é o que permite migrar sem perder agente, skill e memória.

**Não copiar:** multi-tenancy, Postgres, Tauri/Expo, i18n de 14 idiomas, MCP +
Composio. Tudo isso resolve um problema que a Zaya não tem (time distribuído) e
custaria mais do que vale.

---

## 6. Alerta de licença

A Zaya é **MIT**. O agentes.chat é **AGPL-3.0** com licença comercial separada
para uso sem publicar mudanças ou rebrand. Copiar código de lá para cá
contaminaria a Zaya com AGPL — inclusive obrigação de oferecer o fonte a quem
usar pela rede.

Consequência prática: **copie ideias e ADRs, nunca arquivos.** As decisões em
`docs/adr/` são texto de arquitetura, ótimo material de leitura; o código é
outra conversa. Para o `agenteschat.bundle`, o formato é de interoperabilidade —
tudo bem implementar do lado da Zaya, mas confirme se o formato em si está
documentado como aberto antes de tratá-lo como spec.

---

## 7. Veredito

O agentes.chat é o que a Zaya viraria se precisasse servir um time: 60× o
código, servidor, banco, apps e governança. A Zaya é o que sobra quando você
remove tudo isso e mantém só o que faz uma agente *estar presente*: voz local,
memória em arquivo, ritmo diário e domínio próprio.

Os dois já se encontram pelo `agentes-chat-export.json` — o caminho saudável não
é fundir, é manter a Zaya como **agente pessoal local** e deixá-la exportar para
o workspace quando o assunto for time. E, do lado técnico, roubar deles cinco
ideias: aprovação com escopo, teto de orçamento, skills versionadas, evals de
comportamento e isolamento de execução — nessa ordem.
