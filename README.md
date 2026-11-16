# Zaya

Deixei a Zaya aberta num terminal do lado da tela e fui trabalhar. Em algum
momento eu falei alguma coisa em voz alta, ela respondeu, anotou o que eu pedi
e voltou a ficar quieta. Não tem nada de mágico nisso: é um programa em Python
que ouve, pensa com a API de um modelo, mexe nos arquivos se precisar e fala de
volta. Mas depois de um tempo começa a parecer que tem alguém ali, porque ela
lembra do que eu falei semana passada.

Este README é sobre esse programa: o que ele faz, como instalar, e onde ficam
as decisões que eu tomei no caminho.

## O que é

Um assistente pessoal que roda na minha máquina. Sem conta em serviço nenhum
além do provedor do modelo, sem servidor no meio.

O áudio é transcrito localmente pelo `faster-whisper`, em português. Ele nunca
sai do computador. Só o texto da resposta vai para o sintetizador de voz.

O agente é meu, escrito aqui dentro (`zaya/agent/`). Ele fala direto com a API
do modelo, executa as ferramentas localmente e mantém as sessões em disco. Não
depende de nenhum harness de terceiro.

E o que ficou de mais útil depois de meses usando: ela guarda o que aconteceu.
As conversas vão para um SQLite, as notas ficam em Markdown num Second Brain
indexado com FTS5, e no fim do dia tudo aquilo vira uma página de memória.

## Instalar

Precisa de macOS, Python 3.12 (o `faster-whisper` ainda não tem wheel para
3.13), `uv`, `ffmpeg` e PortAudio.

```bash
brew install ffmpeg portaudio
```

```bash
git clone https://github.com/talitadeoa/zaya.git
cd zaya
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

Se quiser o comando `zaya` solto no PATH:

```bash
uv tool install --python 3.12 --editable .
```

Tem um extra para navegador automatizado, que eu só instalo quando preciso:

```bash
uv pip install --python .venv/bin/python -e ".[browser]"
.venv/bin/playwright install chromium
```

## A primeira vez

```bash
zaya config --init
zaya doctor
```

O `config --init` cria `~/.config/zaya/config.toml`. O `doctor` confere o que
está faltando: microfone, PortAudio, chave do provedor, Second Brain. Se algo
não funciona, a resposta quase sempre está ali.

A chave da API vai em `~/.config/zaya/.env`, não no TOML. Pode ser a genérica:

```bash
ZAYA_AGENT_API_KEY=...
```

ou a nativa do provedor que você escolher (`DEEPSEEK_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`). O `.env.example`
tem todas as variáveis, uma por uma, com comentário.

Depois é só rodar `zaya`.

## Escolher o modelo

No `config.toml`:

```toml
[agent]
provider = "deepseek"   # deepseek | openai | openrouter | ollama | anthropic | custom
model = "deepseek-flash"
```

Na prática eu quase nunca edito isso à mão. `/modelo` dentro do app lista os
modelos de todos os provedores que têm chave, e `/modelo 3` ou
`/modelo openai/gpt-4o` troca na hora. O `F6` abre o mesmo seletor. A escolha
fica salva no `config.toml` (com um `.bak` do lado) e vale no turno seguinte.

Se o provedor não estiver na lista de presets, use `custom` e preencha o
`base_url`.

## Conversar

```bash
zaya            # a TUI, que é como eu uso
zaya -old       # a interface antiga, de terminal
zaya --watch    # reinicia sozinha quando eu edito o código
```

A interface padrão é feita em Textual: painéis, histórico, markdown
renderizado, seleção de texto. Foi a parte que eu mais mexi, porque eu passo o
dia olhando para ela.

Para falar, aperto `F` e falo. `V` liga e desliga a voz das respostas. `C` é o
modo contínuo: o microfone fica aberto, eu falo, ela executa, responde e
volta a ouvir sem eu tocar em nada. Ela decide que eu terminei de falar quando
passa um silêncio (cinco segundos por padrão, ajustável). `M` silencia e `Esc`
interrompe a resposta no meio.

Para digitar, `Enter` ou `I`. E aqui tem uma coisa que me incomodava em outros
assistentes: não existe etapa de confirmar a transcrição. O que ela entendeu
vai direto para o agente. Se errou, eu reclamo na próxima frase.

As teclas que eu realmente uso:

- `F1` abre a paleta de comandos, `F2` as sessões antigas
- `F3` ou `Tab` troca de modo, `F6` de modelo
- `F4` liga a seleção de texto, `Ctrl+C` copia, `Ctrl+L` limpa a tela
- `F5` recolhe os painéis quando eu quero a tela inteira para o texto

E quando quero só uma resposta rápida, sem abrir nada:

```bash
zaya ask "o que eu anotei sobre parentesco?"
zaya ask --no-voice "responda apenas: ok"
```

## Os quatro modos

Ela não tem uma personalidade só. Tem quatro, e eu troco no meio do dia,
porque meu dia tem partes bem diferentes.

A **assistente** é o padrão: agenda, rotinas, prioridades, conversa solta.

A **pesquisadora** é para o mestrado. Antropologia social, leitura, fichamento,
metodologia, revisão bibliográfica. Ela é instruída a não inventar citação, o
que parece pouco até a primeira vez que um modelo inventa uma.

A **trabalhadora** cuida de currículo, vagas, entrevistas, negociação. É a que
sabe ler o `~/SecondBrain/cv/playbook-vagas.md` antes de opinar sobre vaga.

A **investidora** olha bolsa, indicadores e orçamento, e explica o raciocínio
em vez de dar recomendação.

Cada modo só injeta um parágrafo no prompt. Não muda nada da infraestrutura, e
o modo ativo fica salvo entre sessões.

Também dá para ensinar preferência: `/preferencia prefiro respostas curtas`, ou
simplesmente falando "lembra que eu prefiro respostas curtas". Isso vai para
`~/SecondBrain/preferences.md` e entra no começo do prompt, que é a parte que o
cache do provedor reaproveita.

## O que ela pode fazer no computador

Sete ferramentas: `read`, `list`, `glob`, `grep`, `write`, `edit`, `bash`.

O catálogo é fixo, igual para todos os modos. Eu tentei filtrar por modo e
desisti: na prática eu nunca sabia de antemão o que ia precisar, e ficar
ligando e desligando ferramenta dava mais trabalho do que deixar tudo
disponível.

O que importa é a permissão. `write`, `edit` e `bash` pedem confirmação: `o`
para uma vez, `a` para sempre, `r` para recusar. `agent.auto_approve` existe,
mas vem desligado, e eu deixo desligado.

```toml
[agent]
tools = true
auto_approve = false
allow_outside_project = true   # preciso disso para ela alcançar ~/SecondBrain
max_steps = 32
```

`/status` mostra o modelo, a sessão e quantos tokens foram no último turno,
quando o provedor informa. `/tokens` lista os turnos anteriores. Foi assim que
eu descobri que metade do meu gasto vinha de histórico velho, e por isso o
histórico é comprimido num resumo quando passa de vinte mensagens.

## O Second Brain

É uma pasta de Markdown, no formato que eu quiser, indexada localmente com
SQLite FTS5.

```markdown
---
tags: [mestrado, campo]
---

# Diário de campo — aldeia

Discussão sobre parentesco com [[Nota sobre Lévi-Strauss]].
#antropologia
```

Links `[[assim]]`, tags no frontmatter e `#tag` no corpo entram no índice.
Criar nota é aditivo e não pergunta nada. Editar ou excluir pergunta, gera
backup em `.zaya/backups` e manda o original para `.zaya/trash` em vez do lixo
do sistema. Ela também nunca escreve fora da raiz configurada.

O contexto não entra em todo turno. Isso seria caro e barulhento. A busca só
acontece quando a minha mensagem tem pista de retomada: "lembra", "aquele
projeto", "o que a gente decidiu". Quando acontece, vêm dois ou três trechos
curtos, com teto de tamanho, só naquele turno.

```bash
zaya search parentesco
zaya note "Diário de campo"
zaya index --force
```

E no fim do dia, às 23h, ela transforma os turnos em uma nota:
`Memoria/Memória AAAA-MM-DD.md`, com síntese, decisões, conversas e registros.
Se eu fechar o app antes disso, ela consolida o que faltou. Se eu abrir no dia
seguinte sem ter fechado, ela recupera o dia anterior. Se o modelo falhar no
meio, cai num resumo local determinístico, porque perder o dia por causa de uma
API fora do ar seria ridículo.

## O que ela faz sozinha

Enquanto está aberta, ela não fica só esperando.

Às 08:00, manda o bom dia com as tarefas do dia, sem as rotinas. Ao meio-dia,
diz o que foi feito, o que falta e como está a rotina da manhã. Segunda, quarta
e sexta, manda a carteira com a cotação de cada ativo. Toda segunda às 09:00,
busca vaga nas fontes abertas, aplica os filtros do playbook, deduplica, pontua
e manda no Telegram.

Nada disso usa token. São rotinas determinísticas que leem o estado e mandam
mensagem. A única parte com modelo é a consolidação da memória.

Ela também puxa conversa: um check-in depois de 45 minutos de silêncio, no
máximo quatro vezes por dia, nunca entre 23h e 8h. Esse limite existe porque a
primeira versão era insuportável.

```toml
[briefing]
morning_at = "08:00"
noon_at = "12:00"

[career]
days = [0]        # 0 = segunda
at = "09:00"

[proactive]
idle_minutes = 45
daily_max = 4
quiet_hours = "23:00-08:00"
```

A rotina de vagas:

```bash
zaya career status
zaya career buscar    # busca, mas não envia
zaya career enviar    # busca e manda no Telegram
```

## Onde ela aparece

**Telegram.** Sem número dedicado.

```bash
zaya telegram updates   # descobre o chat_id
zaya telegram serve     # escuta texto e áudio e responde
```

Pelo Telegram, por padrão, ela só lê arquivos. `write`, `edit` e `bash` são
negados, porque não tem terminal para eu aprovar nada. Se eu quiser liberar,
ligo `auto_approve` **junto com o `allowed_chat_id` fixado**, para o bot não
dar acesso ao primeiro estranho que falar com ele.

**WhatsApp**, pela Cloud API da Meta: `zaya whatsapp send`, `audio`, `serve`.

**Spotify.** `zaya spotify toggle`, `next`, `volume 40`. Ou por voz, que é
como eu uso de verdade.

**Navegador.** Playwright com perfil persistente, então os logins ficam
salvos:

```bash
zaya browser login https://exemplo.com
zaya browser text https://exemplo.com
```

## Comandos

Os que eu decoro: `/ajuda`, `/status`, `/tokens`, `/modelo`, `/modo`,
`/buscar`, `/nota`, `/novo`, `/voz`, `/fala`, `/texto`, `/interromper`,
`/sair`.

Os que existem mas eu esqueço: `/indexar`, `/consolidar [dia]`, `/bolsa`,
`/carteira`, `/metas`, `/painel`, `/foco`, `/tarefas`,
`/lembrar <texto>`, `/lembretes`, `/cancelar <id>`, `/continuo`,
`/rapido`, `/proativo`, `/contexto`, `/historico`, `/sessao`,
`/preferencia`, `/preferencias`, `/confirmar`, `/autoaprovar`, `/chines`,
`/reiniciar`.

`/ajuda` lista tudo com descrição. É o que eu uso quando esqueço.

## Configuração

Tudo em `~/.config/zaya/config.toml`, com segredos só no `.env`:

```bash
zaya config          # mostra, com os segredos redigidos
zaya config --init
zaya config --force
```

As seções são `agent`, `stt`, `tts`, `audio`, `secondbrain`, `interface`,
`memory`, `briefing`, `career`, `proactive`, `focus`, `agenda`, `browser`,
`telegram` e `whatsapp`. Cada campo tem uma variável `ZAYA_*` correspondente.
Se você tem um config antigo com `[opencode]`, ele ainda é lido e migrado.

## Onde ficam as coisas

```text
zaya/
  agent/          loop do agente, providers, ferramentas, sessões
  audio/          microfone, VAD, pré-roll
  stt/            faster-whisper
  tts/            edge-tts, say, limpeza de Markdown
  secondbrain/    índice FTS5, frontmatter, notas
  memory/         conversas e turnos em SQLite
  controller/     intenções, personas, iniciativa, prompt
  interface/      TUI Textual e terminal Rich
data/             zaya.db, sb_index.db, sessions/, career/, logs/
```

## Privacidade

O áudio não sai da máquina. Só o texto da resposta vai para o `edge-tts`, e se
isso incomodar tem `/voz say`, que usa a voz do sistema e fica 100% local.

As chaves ficam no `.env`. O `zaya config` mostra a configuração com elas
redigidas.

E nada do que eu falo é executado direto. Tudo passa pelo agente, com as
ferramentas passando por permissão. `auto_approve` é opt-in.

## Testes

Não precisam de microfone, download nem rede:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/mypy
```

Os planos e as notas de design ficam em `docs/`.

## Uma nota

Escrevi a Zaya porque queria uma assistente que soubesse o que eu estava
fazendo na semana passada. Chat com memória rasa não resolve isso, e serviço na
nuvem resolve ao preço de eu não saber o que acontece com o que eu falo em voz
alta dentro de casa.

O nome é Zaya. O comando também: `zaya`.
