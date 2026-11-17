# Zaya

Assistente pessoal de voz, local, com agente próprio e Second Brain em Markdown.
Roda no macOS e no Linux (inclusive num mini PC headless, comandado pelo
Telegram), fala e ouve em português, executa ferramentas no computador e guarda
memória entre sessões.

- **STT local** (`faster-whisper`, pt-BR) — o áudio não sai da máquina.
- **Agente próprio** — loop de ferramentas, sessões, permissões e streaming.
- **Second Brain** — notas Markdown indexadas com SQLite FTS5.
- **Proatividade** — briefing diário, lembretes, rotina de vagas.

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
git clone https://github.com/talitadeoa/zaya.git
cd zaya

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# opcional: comando `zaya` no PATH
uv tool install --python 3.12 --editable .
```

No Ubuntu/Debian tem um script que faz tudo: dependências de sistema, `uv`,
Python 3.12, venv, o comando `zaya` em `~/.local/bin` e um atalho de tela cheia
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
zaya config --init   # cria ~/.config/zaya/config.toml
zaya doctor          # confere ambiente, provedor, voz e Second Brain
zaya                 # abre a interface
```

Chaves de API vão em `~/.config/zaya/.env` (ou no ambiente), nunca no TOML:

```bash
ZAYA_AGENT_API_KEY=...          # vale para qualquer provedor
# ou as nativas: DEEPSEEK_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, ANTHROPIC_API_KEY
```

Todas as variáveis estão em `.env.example`, comentadas uma a uma.

## Modelo

```toml
[agent]
provider = "deepseek"     # deepseek | openai | openrouter | ollama | anthropic | custom
model = "deepseek-flash"
base_url = ""             # obrigatório apenas em "custom"
```

Para trocar sem editar nada: `/modelo` lista os modelos dos provedores que têm
chave, `/modelo 3` ou `/modelo openai/gpt-4o` troca na hora, `F6` abre o
seletor. A escolha é salva no `config.toml` (com `.bak`) e vale no turno
seguinte. `ZAYA_AGENT_PROVIDER` e `ZAYA_AGENT_MODEL` no ambiente têm prioridade.

## Interface

```bash
zaya                 # TUI (padrão)
zaya -old            # terminal clássico
zaya --ui textual    # força a TUI
zaya --watch         # reinicia quando os .py mudam
```

Numa máquina headless (mini PC, servidor) a TUI só serve para manutenção por
SSH. Nesse caso o canal de uso é o Telegram: `zaya telegram serve` responde
texto e áudio e entrega a iniciativa proativa no chat, sem depender de tela nem
de alto-falante local.

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
zaya ask "resuma o que eu anotei hoje"
zaya ask --no-voice "responda apenas: ok"
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

O motor fica em `zaya/agent/` e fala direto com a API do provedor — sem
servidor intermediário, sem harness externo.

Sete ferramentas: `read`, `list`, `glob`, `grep`, `write`, `edit`, `bash`.
O catálogo é fixo; todos os modos recebem o mesmo conjunto.

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

- Índice FTS5 incremental, offline (`data/sb_index.db`).
- Links `[[Título]]`, tags no frontmatter e `#tag` no corpo.
- Criar nota é aditivo; editar e excluir pedem confirmação, geram backup em
  `.zaya/backups` e movem para `.zaya/trash`.
- Nunca escreve fora da raiz configurada.
- A busca só entra no turno quando a mensagem tem pista de retomada ("lembra",
  "aquele projeto"), com poucos trechos e teto de caracteres.

```toml
[secondbrain]
path = "~/SecondBrain"
context_results = 2
```

```bash
zaya index --force
zaya search parentesco
zaya note "Diário de campo"
```

**Consolidação diária:** às `memory.at` (23:00), os turnos do dia viram
`Memoria/Memória AAAA-MM-DD.md` (Síntese, Decisões, Conversas, Registros).
Consolida o pendente ao sair e recupera o dia anterior se você abrir sem ter
fechado. Se o modelo falhar, cai num resumo local determinístico.

**Sonho:** na janela `memory.dream_at`–`memory.dream_until` (02:00–08:00), se
a Zaya estiver ociosa, ela sonha. O sonho lê a memória do dia, procura no
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

**No dia seguinte** a Zaya avisa, em uma linha, com o que sonhou e a pergunta
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
```

Para sonhar na hora — sobre hoje, ontem ou um dia específico:

```
/sonhar           # hoje
/sonhar ontem     # recupera a noite passada
/sonhar 2026-11-09 forcar
```

## Automações

Determinísticas, sem custo de token:

| Rotina | Quando | O que faz |
|---|---|---|
| Briefing da manhã | 08:00 | tarefas do dia, sem as rotinas |
| Aviso do sonho | 08:00 (até o meio-dia) | o sonho da noite, em uma linha |
| Resumo do meio-dia | 12:00 (até 18:00) | feito, pendente, rotina matinal |
| Carteira | seg/qua/sex | cotações do que está na carteira |
| Vagas | segunda 09:00 | busca, filtra, deduplica, pontua e envia |

```toml
[briefing]
morning_at = "08:00"
noon_at = "12:00"

[career]
days = [0]      # 0 = segunda
at = "09:00"

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
`~/SecondBrain/cv/playbook-vagas.md`; o estado, em `data/career/state.json`.

```bash
zaya career status
zaya career buscar       # busca sem enviar
zaya career enviar       # busca e manda no Telegram
```

## Integrações

**Telegram**

```bash
zaya telegram updates    # descobre o chat_id
zaya telegram me
zaya telegram serve      # escuta texto e áudio e responde
```

Por padrão, pelo Telegram só leitura: `write`, `edit` e `bash` são negados,
porque não há terminal para aprovar. Para liberar, use
`[telegram] auto_approve = true` **com `allowed_chat_id` fixado**.

**WhatsApp** (Cloud API da Meta)

```bash
zaya whatsapp send "bom dia"
zaya whatsapp audio voz.mp3
zaya whatsapp serve
```

**Navegador** (Playwright, perfil persistente com seus logins)

```bash
zaya browser login https://exemplo.com
zaya browser text https://exemplo.com
zaya browser screenshot https://exemplo.com captura.png
```

## Comandos dentro do app

| Comando | Função |
|---|---|
| `/ajuda` · `/status` · `/tokens` | comandos · provedor/modelo/sessão · consumo |
| `/buscar <termo>` · `/nota <título>` | Second Brain |
| `/indexar` · `/novo` · `/consolidar [dia]` · `/sonhar [dia]` | reindexa · nova conversa · memória do dia · sonho noturno |
| `/modelo [nº\|slug]` · `/modo [nome]` | lista/troca modelo · troca persona |
| `/bolsa <ticker>` · `/carteira` | cotação · carteira |
| `/metas` · `/painel` · `/foco` | metas do ciclo · painel · foco |
| `/tarefas [sync\|fazer\|concluir\|limpar]` | tarefas de longo prazo |
| `/lembrar <texto>` · `/lembretes` · `/cancelar <id>` | lembretes |
| `/voz on\|off\|edge\|say\|none` · `/continuo on\|off` | voz e escuta |
| `/fala` · `/texto` · `/interromper` | modo de entrada |
| `/rapido on\|off` · `/proativo on\|off` | modo rápido · proatividade |
| `/contexto` · `/historico` · `/sessao` | contexto, histórico, sessão |
| `/preferencia <texto>` · `/preferencias` | ensina · mostra preferências |
| `/confirmar` · `/autoaprovar` | confirmação de notas · ferramentas |
| `/chines on\|off` · `/reiniciar` · `/sair` | eco em chinês · reinicia · encerra |

`/ajuda` lista tudo com a descrição de cada um.

## Configuração

Arquivo: `~/.config/zaya/config.toml` (segredos só no `.env`).
Seções: `agent`, `stt`, `tts`, `audio`, `secondbrain`, `interface`, `memory`,
`briefing`, `career`, `proactive`, `focus`, `agenda`, `browser`, `telegram`,
`whatsapp`.

```bash
zaya config          # mostra a configuração (segredos redigidos)
zaya config --init   # cria o padrão
zaya config --force  # sobrescreve
```

Cada campo tem uma variável `ZAYA_*` correspondente (veja `.env.example`).
Configurações no formato antigo (`[opencode]`, `ZAYA_OPENCODE_*`) ainda são
aceitas e migradas na leitura.

## Estrutura

```text
zaya/
  agent/          # loop do agente, providers, ferramentas, sessões
  audio/          # microfone, VAD, pré-roll
  stt/            # faster-whisper
  tts/            # edge-tts, say, limpeza de Markdown
  secondbrain/    # índice FTS5, frontmatter, notas
  memory/         # conversas e turnos (SQLite), consolidação e sonho
  controller/     # intenções, personas, iniciativa, prompt
  interface/      # TUI Textual e terminal Rich
  cli*.py         # parser, comandos, runtime
data/             # zaya.db, sb_index.db, sessions/, career/, logs/
```

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
.venv/bin/ruff check .
.venv/bin/mypy
```

Planos e notas de design ficam em `docs/`, incluindo a proposta de execução
headless no Linux via Telegram em `docs/plans/`.
