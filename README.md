# Gaia

Assistente pessoal de voz, local, com agente próprio e Second Brain em Markdown.
Roda no macOS e no Linux (inclusive num mini PC headless, comandado pelo
Telegram), fala e ouve em português, executa ferramentas no computador e guarda
memória entre sessões.

- **STT local** (`faster-whisper`, pt-BR) — o áudio não sai da máquina.
- **Agente próprio** — loop de ferramentas, sessões, permissões e streaming.
- **Second Brain** — notas Markdown indexadas com SQLite FTS5.
- **Proatividade** — briefing diário, lembretes, rotina de vagas.

Para trabalhar no código, veja [`AGENTS.md`](AGENTS.md): comandos, mapa dos
módulos, invariantes e armadilhas. O índice da documentação está em
[`docs/README.md`](docs/README.md).

---

## Requisitos

- macOS ou Linux (Ubuntu/Debian testado; roda também headless, sem tela).
- Python **3.12** (travado: o `faster-whisper` não tem wheel para 3.13+).
- [`uv`](https://docs.astral.sh/uv/), `ffmpeg` e PortAudio.
- Uma chave de API de algum provedor de modelo.

```bash
# macOS
brew install ffmpeg portaudio

# Ubuntu/Debian
sudo apt install ffmpeg libportaudio2 libsndfile1
```

A reprodução de áudio escolhe sozinha o player disponível: `afplay` no macOS;
`ffplay`, `pw-play`, `paplay`, `mpv` ou `aplay` no Linux. A voz do sistema
(`/voz say`) é exclusiva do macOS — no Linux use `/voz edge`.

## Instalação

```bash
git clone https://github.com/talitadeoa/gaia.git
cd gaia

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# opcional: comando `gaia` no PATH
uv tool install --python 3.12 --editable .
```

No Ubuntu/Debian tem um script que faz tudo: dependências de sistema, `uv`,
Python 3.12, venv, o comando `gaia` em `~/.local/bin` e um atalho de tela cheia
para tablet.

```bash
./scripts/install-linux.sh              # instala tudo
./scripts/install-linux.sh --no-apt     # sem sudo/apt
./scripts/install-linux.sh --no-launcher # sem o atalho gráfico
```

Dependências opcionais:

```bash
# navegador automatizado
uv pip install --python .venv/bin/python -e ".[browser]"
.venv/bin/playwright install chromium
```

## Primeiro uso

```bash
gaia config --init   # cria ~/.config/gaia/config.toml
gaia doctor          # confere ambiente, provedor, voz e Second Brain
gaia                 # abre a interface
```

Chaves de API vão em `~/.config/gaia/.env` (ou no ambiente), nunca no TOML:

```bash
GAIA_AGENT_API_KEY=...          # vale para qualquer provedor
# ou as nativas: DEEPSEEK_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, ANTHROPIC_API_KEY
```

As variáveis conhecidas estão em `.env.example`, comentadas uma a uma.

## Modelo

```toml
[agent]
# Presets: deepseek | openai | openrouter | ollama | anthropic |
# commandcode | commandcode-anthropic | opencode | groq | mistral | xai | custom
provider = "deepseek"
model = "deepseek-flash"
base_url = ""             # obrigatório apenas em "custom"
```

A Command Code funciona como um provedor a mais: `provider = "commandcode"`
usa a Provider API dela (modelos DeepSeek/GPT/GLM…) e reaproveita a chave do
`cmd` CLI em `~/.commandcode/auth.json` — normalmente nem precisa colar chave.
Para os modelos Claude dela, use `provider = "commandcode-anthropic"` (mesma
chave, wire Anthropic). Requer plano com acesso à Provider API.

Para trocar sem editar nada: `/modelo` lista os modelos dos provedores que têm
chave, `/modelo 3` ou `/modelo openai/gpt-4o` troca na hora, `F6` abre o
seletor. A escolha é salva no `config.toml` (com `.bak`) e vale no turno
seguinte. `GAIA_AGENT_PROVIDER` e `GAIA_AGENT_MODEL` no ambiente têm prioridade.

## Interface

```bash
gaia                 # TUI (padrão; config interface.ui)
gaia -old            # terminal clássico (Rich)
gaia --ui terminal   # força o terminal
gaia --watch         # reinicia quando os .py mudam
```

Numa máquina headless (mini PC, servidor) a TUI só serve para manutenção por
SSH. Há dois canais possíveis, e **só um processo pode ser dono do banco e do
Telegram**:

- `gaia telegram serve` — o Telegram responde texto e áudio e entrega a
  iniciativa proativa no chat, sem depender de tela.
- `gaia serve` — o motor num processo e as telas em outros (ver abaixo).

## Servidor e cliente remoto

`gaia serve` é a arquitetura de referência em produção: **um** processo é dono
do `gaia.db`, do Second Brain, dos workers proativos e do poller do Telegram, e
publica HTTP+SSE para clientes finos.

```bash
gaia serve                     # escuta no host/porta de [serve]
gaia serve --host 127.0.0.1 --port 8787
gaia serve --no-telegram       # não sobe o poller junto
gaia --remote http://gaia:8787 # cliente interativo (TUI remota)
gaia --remote http://gaia:8787 ask "resuma o dia"
```

- Rotas: `GET /`, `/health`, `/sessions`, `/events` (SSE), `/search`; `POST
  /turn`, `/permission/{id}`, `/abort`.
- Escutar fora do loopback **exige** `GAIA_SERVE_TOKEN`; a guarda recusa subir
  caso contrário.
- A página web em `GET /` já é um cliente completo (streaming, ferramentas,
  aprovação por botão, busca).
- O token vai no `.env`/ambiente, nunca no TOML.
- Unidades systemd prontas em `deploy/gaia-serve.service` e
  `deploy/gaia-serve-tailnet.service.example`; no mini, `gmin serve
  install|cutover|rollback|status|logs`.

**Voz:** `F` fala uma vez, `V` alterna a voz, `C` liga a escuta contínua
(microfone aberto: fala → executa → responde → volta a ouvir), `M` silencia.
A fala termina sozinha depois de `audio.silence_ms` de silêncio.

**Texto:** `Enter` ou `I`. Não há etapa de confirmar transcrição — o que ela
entendeu vai direto para o agente.

| Tecla | Ação |
|---|---|
| `F1` | paleta de comandos |
| `F2` | sessões anteriores |
| `F3` / `Tab` | trocar de modo |
| `F4` | alternar seleção de texto |
| `F5` | recolher/abrir os painéis |
| `F6` | seletor de modelo |
| `Ctrl+C` | copiar seleção |
| `Ctrl+L` | limpar a tela |
| `Esc` | interromper a resposta |

Execução única, sem interface:

```bash
gaia ask "resuma o que eu anotei hoje"
gaia ask --no-voice "responda apenas: ok"
```

## Modos

Troca com `/modo <nome>`, `Tab` ou `F3`. Cada modo injeta uma diretriz no prompt
e fica salvo entre sessões.

| Modo | Para quê |
|---|---|
| `assistente` (padrão) | agenda, rotinas, prioridades, conversa |
| `pesquisadora` | mestrado, leitura, fichamento, escrita, metodologia |
| `trabalhadora` | currículo, vagas, entrevistas, negociação |
| `investidora` | bolsa, indicadores, orçamento, risco |

O padrão se configura em `[interface] default_mode`.

## Agente e ferramentas

O motor fica em `gaia/agent/` e fala direto com a API do provedor — sem
servidor intermediário, sem harness externo.

Sete ferramentas de base: `read`, `list`, `glob`, `grep`, `write`, `edit`,
`bash` — iguais em todos os modos.

Quando `agenda.env_file` tem credencial válida, entram mais cinco, específicas
da Agenda (não dependem de shell):

| Ferramenta | Para quê |
|---|---|
| `agenda_dia` | tarefas pendentes (com id), feitas e placar da rotina do dia |
| `agenda_rotinas` | rotinas cadastradas e os dias de cada uma |
| `agenda_paginas` | lista páginas da Agenda ou lê uma pelo título |
| `agenda_nova_tarefa` | cria tarefa (exige `confirm=true`) |
| `agenda_marcar_tarefa` | marca/desmarca por id ou texto (exige `confirm=true`) |

E uma de leitura de planilhas, quando o SecondBrain está configurado:

| Ferramenta | Para quê |
|---|---|
| `planilha` | lista abas e tamanhos, mostra uma faixa de linhas ou busca um termo nos `.xlsx` de `~/SecondBrain/planilhas` |

O `.xlsx` é lido com a biblioteca padrão (`zipfile` + XML), sem `openpyxl` e
sem shell — funciona no canal do Telegram mesmo com `shell_access = false`.

```toml
[agent]
tools = true
auto_approve = false              # escrita e shell pedem confirmação
allow_outside_project = true      # necessário para alcançar o Second Brain
max_steps = 32
request_timeout = 900
stall_timeout = 90
```

- Ações que alteram algo pedem confirmação (`o` uma vez, `a` sempre, `r` rejeita).
- O histórico histórico é comprimido num resumo ao passar de
  `max_history_messages` / `max_history_chars`.
- O prompt é montado em duas camadas: prefixo estável (persona e preferências,
  aproveitado pelo cache) e bloco dinâmico (data, hora, modo).
- `/status` mostra modelo, sessão e tokens (quando o provedor informa usage);
  `/tokens` lista os últimos turnos.

## Second Brain

Uma pasta Markdown que é sua, indexada localmente.

```markdown
---
tags: [mestrado, campo]
---

# Diário de campo — aldeia

Discussão sobre parentesco com [[Nota sobre Lévi-Strauss]].
#antropologia
```

- Índice FTS5 incremental, offline, em `sb_index.db` dentro do diretório de
  dados (`~/.local/share/gaia` por padrão; `GAIA_DATA_DIR` muda).
- Links `[[Título]]`, tags no frontmatter e `#tag` no corpo.
- Criar nota é aditivo; editar e excluir pedem confirmação, geram backup em
  `.gaia/backups` e movem para `.gaia/trash`.
- Nunca escreve fora da raiz configurada.
- A busca só entra no turno quando a mensagem tem pista de retomada ("lembra",
  "aquele projeto"), com poucos trechos e teto de caracteres.

```toml
[secondbrain]
path = "~/SecondBrain"
context_results = 2
```

```bash
gaia index --force
gaia search parentesco
gaia note "Diário de campo"
```

**Consolidação diária:** às `memory.at` (23:00), os turnos do dia viram
`Memoria/Memória AAAA-MM-DD.md` (Síntese, Decisões, Conversas, Registros).
Consolida o pendente ao sair e recupera o dia anterior se você abrir sem ter
fechado. Se o modelo falhar, cai num resumo local determinístico.

**Sonho:** na janela `memory.dream_at`–`memory.dream_until` (02:00–08:00), se
a Gaia estiver ociosa, ela sonha. O sonho lê a memória do dia, procura no
índice as memórias antigas que ressoam com ele e escreve
`Sonhos/Sonho AAAA-MM-DD.md` — parágrafo onírico, **Ecos do dia**,
**Ressonâncias**, **Pergunta para amanhã** e a lista de memórias revisitadas
(no máximo dois trechos por nota, sem repetir sonhos nem a memória do dia).
A nota já nasce ligada à memória do dia (`[[Memória AAAA-MM-DD]]`).

Os sonhos ficam em pasta própria, separada do diário consolidado, para que
`/buscar` não misture sonho com registro. De madrugada o sonho é sobre o dia
que acabou; à noite (se a janela começar antes da meia-noite) é sobre o dia em
curso. Se o modelo falhar, a nota sai de qualquer forma com a costura
determinística do dia com as notas antigas.

**No dia seguinte** a Gaia avisa, em uma linha, com o que sonhou e a pergunta
que ficou — sem segunda chamada ao modelo, fora das quiet hours, uma vez por
dia. O aviso sai pelo canal em uso (TUI ou Telegram) e não conta como
iniciativa proativa.

```toml
[memory]
dream_enabled = true
dream_at = "02:00"        # início da janela noturna (pode virar a meia-noite)
dream_until = "08:00"     # fim da janela
dream_folder = "Sonhos"   # pasta própria, fora do diário consolidado
dream_notify = true       # avisa no dia seguinte
dream_notify_at = "08:00" # depois disso o aviso pode sair
retry_minutes = 10.0      # espera entre tentativas se o envio falhar
```

Para sonhar na hora — sobre hoje, ontem ou um dia específico:

```
/sonhar                  # hoje
/sonhar ontem            # recupera a noite passada
/sonhar 2026-11-09 forcar
/sonhar descartar ontem  # esquece o sonho (nota vai para a lixeira)
```

O sonho retoma os dois sonhos anteriores como fio narrativo, então há
continuidade entre as noites. Se o modelo devolver texto fora do formato
pedido, a prosa dele é mantida e a moldura (Ecos, Ressonâncias, Pergunta) é
reconstruída — o aviso nunca fica sem conteúdo sem avisar no log.

## Automações

Horário e determinísticas na maior parte — sem custo de token, **exceto** o
insight do fecho da noite (`briefing.evening_insight`) e o comentário opcional
do briefing (`briefing.personalize`), que chamam o modelo.

| Rotina | Quando | O que faz |
|---|---|---|
| Briefing da manhã | 08:00 | tarefas do dia, sem rotinas |
| Aviso do sonho | 08:00 (quando as quiet hours acabam) | o sonho da noite, em uma linha |
| Resumo do meio-dia | 12:00 (até 18:00) | feito e pendente, sem rotinas |
| Fecho da noite | 18:00 (até 23:00) | feito, não feito, rotina matinal e insight do modelo |
| Carteira | seg/qua/sex | cotações do que está na carteira |
| Vagas | segunda 09:00 (até 18:00) | busca, filtra, deduplica, pontua e envia |

```toml
[briefing]
enabled = true
morning_at = "08:00"
noon_at = "12:00"
noon_until = "18:00"
morning_routine = "matinal"   # usada no fecho da noite
max_tasks = 15
evening_enabled = true
evening_at = "18:00"
evening_until = "23:00"
evening_insight = true        # chama o modelo
market_enabled = true
market_days = [0, 2, 4]       # seg/qua/sex
market_at = "12:00"

[career]
enabled = true
days = [0]      # 0 = segunda
at = "09:00"
until = "18:00"
max_items = 8

[proactive]
idle_minutes = 45
daily_max = 4
quiet_hours = "23:00-08:00"

[memory]
at = "23:00"
use_llm = true
dream_enabled = true
dream_at = "02:00"
dream_until = "08:00"
dream_notify = true
dream_notify_at = "08:00"
```

O método da rotina de vagas (fontes, filtros, rubrica de score) vive em
`~/SecondBrain/cv/playbook-vagas.md`; o estado, em `career/state.json` dentro do
diretório de dados (`~/.local/share/gaia` por padrão).

```bash
gaia career status
gaia career buscar [--json]   # busca sem enviar
gaia career enviar            # busca e manda no Telegram
```

## Integrações

**Telegram**

```bash
gaia telegram updates    # descobre o chat_id
gaia telegram me
gaia telegram send "bom dia" [--chat ID]
gaia telegram audio voz.mp3 [--voice] [--chat ID]
gaia telegram poll [--echo]   # long polling, sem o agente
gaia telegram serve      # escuta texto e áudio e responde
```

`auto_approve = true` (com `allowed_chat_id` fixado) aprova no chat as
ferramentas que pedem confirmação — inclusive as `agenda_*`, que é como a Gaia
consulta e altera a Agenda pelo Telegram.

`bash`, `write` e `edit` ficam **negados por padrão mesmo com `auto_approve`**:
`shell_access = true` é o único jeito de liberar, e só vale junto de
`auto_approve` + `allowed_chat_id`. Com o padrão, quem controlar o chat mexe na
Agenda — não roda comando na máquina.

```toml
[telegram]
auto_approve = true
shell_access = false   # mantenha desligado: shell remoto é o risco maior
```

**WhatsApp** (Cloud API da Meta)

```bash
gaia whatsapp send "bom dia"
gaia whatsapp audio voz.mp3
gaia whatsapp serve
```

**Navegador** (Playwright, perfil persistente com seus logins)

```bash
gaia browser login https://exemplo.com
gaia browser open https://exemplo.com
gaia browser text https://exemplo.com
gaia browser html https://exemplo.com
gaia browser screenshot https://exemplo.com captura.png
```

## Comandos dentro do app

| Comando | Função |
|---|---|
| `/ajuda` · `/status` · `/tokens` | comandos · provedor/modelo/sessão · consumo |
| `/buscar <termo>` · `/nota <título>` | Second Brain |
| `/indexar` · `/novo` · `/consolidar [dia]` · `/sonhar [dia]` | reindexa · nova conversa · memória do dia · sonho noturno |
| `/modelo [nº\|slug]` · `/modo [nome]` | lista/troca modelo · troca persona |
| `/bolsa <ticker>` · `/carteira [add\|remover <ticker>]` | cotação · carteira |
| `/metas` · `/painel` · `/foco [definir\|limpar]` | metas do ciclo · painel (`/painel` é alias de `/metas`) · foco |
| `/tarefas [sync\|fazer\|concluir\|limpar]` | tarefas de longo prazo |
| `/lembrar <texto>` · `/lembretes` · `/cancelar <id>` | lembretes |
| `/voz on\|off\|edge\|say\|none` · `/continuo on\|off` | voz e escuta |
| `/fala` · `/texto` · `/interromper` | modo de entrada |
| `/rapido on\|off` · `/proativo on\|off` | modo rápido · proatividade |
| `/pensando on\|off` | mostra/esconde o raciocínio na tela (o Telegram tem interruptor próprio) |
| `/contexto` · `/historico` · `/sessao` | contexto, histórico, sessão |
| `/preferencia <texto>` · `/preferencias` | ensina · mostra preferências |
| `/confirmar` · `/autoaprovar` | confirmação de notas · ferramentas |
| `/chines on\|off` · `/reiniciar` · `/sair` | eco em chinês · reinicia · encerra |

`/ajuda` lista tudo com a descrição de cada um.

## Configuração

Arquivo: `~/.config/gaia/config.toml` (segredos só no `.env`).
Seções: `agent`, `stt`, `tts`, `audio`, `secondbrain`, `interface`, `memory`,
`briefing`, `career`, `proactive`, `focus`, `agenda`, `browser`, `telegram`,
`whatsapp`, `serve`.

```bash
gaia config          # mostra a configuração (segredos redigidos)
gaia config --init   # cria o padrão
gaia config --force  # sobrescreve
```

A maioria dos campos tem uma variável `GAIA_*` correspondente (veja
`.env.example` e `_ENV_MAP` em `gaia/config.py`).
Configurações no formato antigo (`[opencode]`, `GAIA_OPENCODE_*`) ainda são
aceitas e migradas na leitura.

## Estrutura

```text
gaia/
  agent/          # loop do agente, providers, ferramentas, sessões
  audio/          # microfone, VAD, pré-roll
  stt/            # faster-whisper
  tts/            # edge-tts, say, limpeza de Markdown
  secondbrain/    # índice FTS5, frontmatter, notas, embeddings opcionais
  memory/         # conversas e turnos (SQLite), consolidação e sonho
  briefing/       # composição dos textos de manhã, meio-dia e noite
  controller/     # orquestrador, turnos, comandos, personas, iniciativa
  interface/      # TUI Textual, terminal Rich e RemoteUI
  serve/          # HTTP+SSE do `gaia serve` e página web
  client/         # cliente `gaia --remote`
  agenda/         # cliente da Agenda (Supabase)
  career/         # rotina de vagas
  finance/        # cotações e carteira
  tasks/          # tarefas de longo prazo
  telegram/       # bot e serviço do Telegram
  whatsapp/       # Cloud API da Meta
  browser/        # Playwright
  cli*.py         # parser, comandos, runtime, integrations, serve
  focus.py        # foco e lua
```

Dados (`gaia.db`, `sb_index.db`, `sessions/`, `career/`, `logs/`) ficam em
`~/.local/share/gaia` por padrão, fora do repo. O launcher `Abrir Gaia.command`
define `GAIA_DATA_DIR="$REPO/data"` para usar `./data`.

## Privacidade

- Áudio nunca sai da máquina; é transcrito localmente.
- Só o texto da resposta vai para o TTS `edge`. Use `/voz say` (macOS) para
  ficar 100% offline.
- Chaves apenas em `.env` ou no ambiente; o `config` as mostra redigidas.
- Nada da transcrição é executado direto: tudo passa pelo agente e pelas
  permissões.
- `agent.auto_approve` é opt-in e vem desligado.

## Desenvolvimento

```bash
.venv/bin/python -m pytest        # sem microfone, download ou rede
.venv/bin/ruff check gaia tests
.venv/bin/mypy
```

Os três comandos são exatamente o que o CI roda. Para trabalhar no código, leia
`AGENTS.md` (mapa, invariantes e armadilhas). Planos e notas de sessão ficam em
`docs/`; `docs/README.md` classifica o que é referência viva, plano ou
histórico.
