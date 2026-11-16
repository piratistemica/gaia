# Plano de limpeza e refatoração da Zaya

Levantamento feito em 2026-11-14 sobre o estado atual do repositório: 18 666 linhas em
`zaya/`, 6 607 em `tests/`, 365 testes passando, `ruff check` limpo, `ruff format` com
50 arquivos pendentes e 4 erros de mypy.

O objetivo é reduzir superfície: menos código que nunca roda, menos feature que nunca
foi usada, menos arquivo esquecido. **Nada aqui foi executado ainda.** Cada etapa termina
em um comando de verificação e o diff fica aberto para revisão antes do commit.

## Regras que valem para todas as etapas

- Um commit por etapa, mensagem em português, no padrão do repositório (`fix:`, `chore:`,
  `refactor:`).
- Antes de cada commit: `pytest -q` verde + `ruff check zaya tests` limpo.
- Nunca apagar arquivo de teste sem substituir a cobertura que ele dava — quando a
  feature sai, o teste sai; quando o código só muda de lugar, o teste fica.
- `data/` e `config/` estão no `.gitignore`: mexer neles é limpeza de disco, não de git.
  Nesses casos, listar os caminhos antes de apagar e nunca usar `rm -rf` em diretório
  que o código ainda referencia.
- Nada de "limpar de passagem": se uma etapa encontrar código morto fora do escopo, ele
  entra numa lista no fim do documento em vez de entrar no mesmo commit.

---

## 1. Diagnóstico

### 1.1 Features inteiras

| Feature | Arquivos | Linhas | Evidência de desuso |
| --- | --- | --- | --- |
| Spotify | `zaya/spotify/*.py` (3) + `tests/test_spotify.py` | 294 | 0 ocorrências em `data/logs/zaya.log`; depende de AppleScript (macOS only) |
| WhatsApp Cloud API | `zaya/whatsapp/*.py` (3) + `tests/test_whatsapp.py` | 405 | `ZAYA_WHATSAPP_*` ausentes do `~/.config/zaya/.env`; 0 no log; nunca configurado |
| Browser / Playwright | `zaya/browser/*.py` (2) + `tests/test_browser.py` | 144 | `import playwright` falha no `.venv` → `zaya browser` sempre morre no aviso de instalação; 43% de cobertura |
| Eco em chinês | espalhado (ver 1.3) | ~60 | 0 no log; cria sessão `Zaya — 中文` no provedor a cada mensagem |

Total de 1.1 (sem o chinês): **843 linhas de produção + 211 de teste**.

### 1.2 Código morto (funções que nada chama)

| Função | Arquivo:linha | Linhas | Observação |
| --- | --- | --- | --- |
| `_ensure_session` | `zaya/controller/orchestrator.py:477` | 5 | o próprio docstring diz "mantido para compatibilidade" |
| `_voice_input` | `zaya/controller/voice.py:377` | 13 | `_capture_voice` é quem roda |
| `force_start` | `zaya/audio/recorder.py:307` | 9 | zero chamadas |
| `force_end` | `zaya/audio/recorder.py:317` | 8 | zero chamadas |
| `is_streaming` | `zaya/interface/terminal.py:133` | 2 | zero chamadas |
| `palette_labels` | `zaya/interface/textual_app.py:942` | 2 | zero chamadas |
| `_content_text` | `zaya/agent/providers/openai_compat.py:51` | 10 | zero chamadas |
| `uncached_input` | `zaya/agent/providers/base.py:81` | 2 | zero chamadas |
| `set_title` | `zaya/agent/runtime.py:202` | 4 | API do `AgentClient` sem consumidor |
| `list_sessions` | `zaya/agent/runtime.py:207` | 14 | idem (o picker usa `Orchestrator.sessions()`) |
| `is_idle` | `zaya/agent/runtime.py:227` | 2 | idem |

**~71 linhas.** `session_status`, que é pública e testada, **não** entra.

Morto mas com histórico dentro: `_adopt_legacy_history()` (`orchestrator.py:168`) existe
para migrar conversas de `~/zora`. `~/zora` não existe nesta máquina; hoje é um `stat`
inútil a cada boot. Fica para a etapa 5, porque é decisão de produto (se você voltar a
usar o nome antigo, ele serve).

Arquivo inteiro morto: `zaya/interface/ascii_art.py` — `DIVINE`, `DIVINE_LINES` e
`fit_braille` não são importados por ninguém. 134 linhas, 0% de cobertura.

### 1.3 Duplicação

| O que | Onde | Ação |
| --- | --- | --- |
| `_error_message(status, body)` copiado idêntico (12 linhas) | `providers/openai_compat.py:213` e `providers/anthropic.py:240` | mover para `providers/base.py` |
| Lista de comandos em dois lugares | `interface/terminal.py:600+` e `interface/textual_render.py:124` (`_HELP`) | eleger `_HELP` como fonte e derivar o resto |
| `_toggle_chinese` com lógicas diferentes | `controller/commands.py:606` e `interface/textual_app.py:1179` | sai junto com o chinês (etapa 3) |

O `_toggle_chinese` duplicado é sintoma: só o de `commands.py` persiste no config, o da
TUI não. Se o chinês ficasse, esse bug teria que ser corrigido.

### 1.4 Sujeira de repositório e disco

| Item | Tamanho | Situação |
| --- | --- | --- |
| `ref.png` | 22 KB | **versionado no git**, nada referencia (nem README, nem docs) |
| `config/config.toml.bak` | 4,5 KB | idêntico ao `config.toml` atual (0 linhas de diff) |
| `config/config.toml.bak-2026*` (3) | ~11 KB | backups manuais antigos |
| `data/opencode-data/` | **28 MB** | run antigo do opencode (`repos/`, `log/`, `opencode.db`) — 80% de `data/` |
| `data/opencode-export/` | 296 KB | 27 `.md` de sessões antigas (inclui as da época do nome "zora") |
| `data/tmp/*.mp3` | 252 KB | 7 áudios temporários esquecidos |
| `data/browser/Default` | 1,8 MB | perfil de navegador que nunca foi usado |
| `data/servers/4629.json` + `.lock` | 309 B | lock órfão de 11/11 |

### 1.5 Qualidade

- `ruff format --check`: **50 arquivos** fora do padrão.
- `mypy`: **4 erros** — `textual_markdown.py:86` (tipo do `Heading`) e
  `textual_app.py:212, 990, 999` (`ALLOW_SELECT` atribuído na instância).
- Maior função do projeto: `_run_turn` em `agent/runtime.py:505` com **174 linhas**.
- `config.py` tem 996 linhas porque os defaults (models Pydantic) e o template TOML
  (`_DEFAULT_CONFIG_TEMPLATE`, linha 720) são escritos duas vezes.

---

## 2. Etapa 1 — Lixo mecânico (risco zero)

Nada disso muda comportamento. Pode ir num único commit `chore(limpeza): remove código e
arquivos mortos`.

- [ ] Apagar `zaya/interface/ascii_art.py` (134 linhas, 0 importadores).
- [ ] Remover as 11 funções da tabela 1.2. Exceção a avaliar: `force_start`/`force_end`
      (`audio/recorder.py`) são a interface pronta para um botão de "falar agora" na TUI —
      se pretende usá-las, ficam; hoje nada chama.
- [ ] **Não** remover `session_status` (público, usado em `orchestrator.py:547`, coberto
      por `tests/test_agent.py`) nem a tabela `reminders`/`MemoryStore`, que aparecem perto
      no grep mas são de outro fluxo.
- [ ] Remover `_error_message` dos dois providers e criar um helper em
      `providers/base.py`; ajustar os dois chamadores.
- [ ] `git rm ref.png`.
- [ ] Apagar os 4 `config/config.toml.bak*` (o `.bak` sem data é idêntico ao atual).
- [ ] Rodar `ruff format zaya tests` (os 50 arquivos) e conferir que o diff é só estilo.
- [ ] Corrigir os 4 erros de mypy (`textual_markdown.py:86` e `ALLOW_SELECT` em
      `textual_app.py`), que hoje fariam o job `lint` do CI falhar com a config do
      `pyproject.toml`.

**Verificação**

```bash
pytest -q                 # 365 passando (nada deve mudar)
ruff check zaya tests     # All checks passed
mypy                      # Success: no issues found
ruff format --check zaya tests   # 0 pendentes
```

**Saída esperada:** ~205 linhas de produção e 134 de arte fora; diff de formatação em 50
arquivos.

---

## 3. Etapa 2 — Spotify, WhatsApp e Browser

Aqui é remoção de feature. Cada uma toca 4 camadas: pacote, CLI, orquestrador (só
Spotify) e config.

### 3.1 Spotify (~294 linhas)

- [ ] Apagar `zaya/spotify/` e `tests/test_spotify.py`.
- [ ] `zaya/controller/turns.py`: remover os 3 imports (linhas 42–44), o bloco em
      `_dispatch_silent` (74–76), o bloco em `_dispatch` (95–97) e `_handle_spotify`
      (131–139).
- [ ] `zaya/cli_parser.py:90–95`: remover o subparser `spotify`.
- [ ] `zaya/cli.py:18`, `60–61`: remover import e branch.
- [ ] `zaya/cli_integrations.py:12–13, 137–158`: remover imports e `_spotify_command`.
- [ ] `pyproject.toml` **não** é afetado (Spotify usa `subprocess` do sistema).
- [ ] `README.md`: linha 43 ("controla o Spotify"), o exemplo das 18:30 (linha 77) e a
      seção "No Spotify" (linha 335).
- [ ] Conferir que nenhum outro módulo fala com o player do sistema:
      `grep -rn "playpause\|osascript" zaya --include=*.py` deve sobrar só em `spotify/`.

Não há nada em `config.py` nem no `.env.example` (confirmado: `grep -i spotify` vazio nos
dois).

### 3.2 WhatsApp (~405 linhas)

- [ ] Apagar `zaya/whatsapp/` e `tests/test_whatsapp.py`.
- [ ] `zaya/cli_parser.py:61–70`: remover o subparser `whatsapp`.
- [ ] `zaya/cli.py:20`, `56–57`: remover import e branch.
- [ ] `zaya/cli_integrations.py:16–17, 20–43, 45–50, 204–221`: remover imports,
      `_whatsapp_command`, `_message_id` (só é usado nos dois envios de WhatsApp, linhas
      35 e 38) e `_serve_whatsapp`.
- [ ] `zaya/config.py`: remover `WhatsAppConfig` (269–282), o campo `whatsapp` em
      `Config` (349), o bloco de redação em `public_dict` (422–424), as 8 entradas
      `ZAYA_WHATSAPP_*` do `_ENV_MAP` (593–600) e a seção `[whatsapp]` do template
      (822–829).
- [ ] `.env.example:117–129`: remover o bloco "WhatsApp Cloud API".
- [ ] `README.md:327–333`: remover a seção.
- [ ] `tests/test_cli.py:15–20`: remover `test_parser_whatsapp_serve_defaults` (é o único
      teste que exercita o subparser; sem ele o parser ficaria sem essa cobertura de
      propósito).
- [ ] `tests/test_config.py`: o grep não achou `whatsapp` hoje, mas os testes de
      `public_dict`/redação podem tocar o campo — confirmar rodando antes de apagar.

### 3.3 Browser (~144 linhas)

Antes de apagar, decidir: `zaya browser login` é o único caminho documentado para
mantê-la logada em sites com Playwright. Hoje ele nem roda (falta o pacote). Se a
resposta for "não uso", remover tudo; se for "talvez", a alternativa barata é **manter o
código e instalar o extra** (`uv pip install --python .venv/bin/python -e ".[browser]"`)
para o comando deixar de ser uma armadilha.

Se remover:

- [ ] Apagar `zaya/browser/` e `tests/test_browser.py`.
- [ ] `zaya/cli_parser.py:97–110`: remover o subparser `browser`.
- [ ] `zaya/cli.py:16`, `62–63`: remover import e branch.
- [ ] `zaya/cli_integrations.py:8, 160–202`: remover import e `_browser_command`.
- [ ] `zaya/config.py`: `BrowserConfig` (294–302), campo `browser` (353),
      `browser_profile_dir()` (396–399), 4 entradas `ZAYA_BROWSER_*` (493–496) e a seção
      `[browser]` do template (831–835).
- [ ] `pyproject.toml`: remover o extra `browser` (playwright).
- [ ] `.env.example:144–147` e `README.md:337–346`.

**Verificação (3.1–3.3)**

```bash
pytest -q                                   # 365 - 13 = 352 passando
ruff check zaya tests && mypy
zaya --help                                  # sem spotify/whatsapp/browser
```

Rodar `uv run zaya doctor` para garantir que nada no diagnóstico dependia desses módulos.

---

## 4. Etapa 3 — Eco em chinês (~60 linhas)

Decisão de produto: não é bug, é escopo. Cria uma sessão de modelo extra por mensagem e
o `/chines` aparece na ajuda e na paleta (F1).

- [ ] `zaya/config.py`: `chinese_echo` (179), entrada `ZAYA_CHINESE` (500) e a linha no
      template (818).
- [ ] `zaya/controller/orchestrator.py:131–132`: os caches `_zh_session_id` e `_zh_cache`.
- [ ] `zaya/controller/commands.py`: branch `/chines` (128–129), `_toggle_chinese`
      (606–613) e `translate_zh` inteiro (615–648). `asyncio` continua importado — é usado
      em outras passagens do arquivo (53, 62, 69, 422).
- [ ] `zaya/interface/textual_app.py`: `_echo_zh` (1169–1177), `_toggle_chinese`
      (1179–1190), as duas chamadas em `finish_stream` e `_write_user` (1132–1133,
      1163–1164), a entrada da paleta (937), o branch da paleta (969) e o branch
      `/chines` (654–655).
- [ ] `zaya/interface/textual_render.py:135` e `zaya/interface/terminal.py:614`:
      remover a linha da ajuda.
- [ ] `tests/test_textual_app.py`: o fake `translate_zh` (144) e o campo `chinese_echo` no
      `SimpleNamespace` da config falsa (102).

**Verificação:** `pytest -q`, e abrir a TUI conferindo que a paleta não tem mais o item.

---

## 5. Etapa 4 — Limpeza de disco (~30 MB)

Fora do git (`data/` é ignorada). Fazer com a Zaya fechada.

- [ ] `data/opencode-data/` — 28 MB. Conferir antes:
      `ls data/opencode-data/opencode/repos`.
- [ ] `data/opencode-export/` — 27 arquivos `.md`; se algum tiver valor, mover para
      `data/notes/` antes.
- [ ] `data/tmp/*.mp3` — áudios de TTS já enviados.
- [ ] `data/browser/` — só se o browser saiu na etapa 3.3.
- [ ] `data/servers/4629.{json,lock}` — conferir que não há processo usando
      (`lsof data/servers/4629.lock`).

**Não tocar:** `data/zaya.db*`, `data/sb_index.db*`, `data/sessions/`,
`data/logs/zaya.log` (é o histórico de diagnóstico), `data/notes/`, `data/career/`.

---

## 6. Etapa 5 — Refatoração de verdade

Cada item é um commit próprio e merece teste rodando entre eles.

1. **`config.py` (996 linhas).** Gerar `_DEFAULT_CONFIG_TEMPLATE` a partir dos models
   (`model_json_schema()` + um formatador) em vez de manter as duas listas na mão. Ganho:
   o template nunca mais divergir do código. Risco: médio — o template hoje tem
   comentários escritos à mão que o gerador precisa preservar. Alternativa mais barata:
   só adicionar um teste que compara as chaves do template com os campos dos models e
   falha no CI quando divergirem.
2. **`_run_turn` (`agent/runtime.py:505`, 174 linhas).** Quebrar em
   `_prepare_turn`, `_stream_provider`, `_apply_tool_calls`, `_finalize_turn`. É a função
   mais crítica do projeto — exigir `tests/test_orchestrator.py` e
   `tests/test_turns_events.py` verdes antes e depois, sem alterar teste.
3. **`_adopt_legacy_history` (`orchestrator.py:168`).** Manter ou remover — depende de
   você voltar a usar `~/zora` algum dia.
4. **Ajuda unificada.** `terminal.py` e `textual_render._HELP` descrevem os mesmos
   comandos. Eleger uma lista (nome, descrição) e formatar as duas saídas a partir dela.
5. **`cli_integrations.py` (338 linhas → ~110 depois das etapas 2 e 3).** Sobra só
   Telegram + career. Renomear para `cli_telegram.py`? Só se o nome "integrações" passar a
   mentir.

---

## 7. Ordem sugerida e esforço

| Etapa | Risco | Diff | Ganho |
| --- | --- | --- | --- |
| 1 — lixo mecânico | baixo | ~340 linhas | tira código morto e faz o CI valer |
| 2 — Spotify/WhatsApp | baixo | ~840 linhas | maior redução de superfície |
| 2b — Browser | baixo | ~180 linhas | mas decidir antes (ver 3.3) |
| 3 — chinês | baixo | ~60 linhas | menos escopo na ajuda/paleta |
| 4 — disco | nenhum | 30 MB | não muda código |
| 5 — refatoração | médio/alto | variável | manutenção |

Sugestão de sequência: **1 → 3 → 2 → 4 → 5**. O chinês vem antes do Spotify porque é o
menor diff e valida o processo (branch, commit, verificação) antes das remoções grandes.

## 8. Decisões pendentes

1. **Spotify:** remover de vez? (não há config nem teste de integração — é barato voltar)
2. **WhatsApp:** remover? Nunca foi configurado.
3. **Browser:** remover ou instalar o extra e passar a usar?
4. **Chinês:** remover?
5. **`~/zora`:** o código de adoção do histórico antigo fica ou sai?

## 9. Fora de escopo (achados que não entram aqui)

- `zaya/controller/contract.py` (284 linhas de declarações para o mypy) é feio mas
  funcional; mexer nisso exige repensar o padrão de mixins do `Orchestrator`.
- `tests/test_orchestrator.py` (973 linhas) e `tests/test_token_budget.py` (780) são
  grandes; dividir não reduz risco, só muda de lugar.
- Cobertura geral em 59% — subir cobertura é outro projeto, não limpeza.
