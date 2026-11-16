# Design — Zaya no mini PC Ubuntu, controlada do Mac via Telegram

**Data:** 2026-11-16
**Status:** aprovado

## Contexto

A Zaya foi escrita para morar num Mac com microfone e alto-falante. A usuária comprou
um mini PC com Ubuntu que ficará guardado (sem tela, teclado ou microfone úteis) e quer
controlá-lo do MacBook. Isso muda duas premissas do produto:

1. **O canal principal deixa de ser a TUI/terminal** e passa a ser o Telegram.
2. **A máquina é headless**: nada de janela de navegador nem de áudio local.

## Escopo aprovado

- Conversar, memória, tarefas, agenda e Telegram.
- Navegador (Playwright) com perfil persistente e logins.
- Iniciativa proativa (bom dia, lembretes, avisos de agenda) entregue **pelo Telegram**.
- Remoção completa do Spotify.
- Fora de escopo: notificações nativas do SO, WhatsApp, Docker, app gráfico no mini.

## Arquitetura

```
MacBook / celular  ⇄  Bot do Telegram (API)  ⇄  mini PC Ubuntu (headless)
                                                └─ systemd: zaya telegram serve
                                                     ├─ STT local (faster-whisper, CPU/int8)
                                                     ├─ TTS edge (nuvem) para resposta em voz
                                                     ├─ Navegador headless (Playwright,
                                                     │  perfil persistente em ~/.local/share/zaya/browser)
                                                     └─ memória (SQLite + Markdown)
```

O processo é único e de longa duração (`zaya telegram serve`), supervisionado pelo
systemd. A TUI continua existindo, mas só é usada em manutenção via SSH.

## Decisões

### 1. Canal como noção de primeira classe

O `Orchestrator` já recebe `channel: str` (`cli` | `telegram`) e usa isso para separar
histórico e modo. Vamos usar o mesmo sinal para decidir **como entregar a fala**:

- `channel == "cli"` → `ui.proactive()` + TTS local (comportamento atual).
- `channel == "telegram"` → `telegram.send_message()`; sem TTS local (a voz já é feita
  por `telegram.voice_replies`).

Isso evita mexer em fluxo de áudio/recorder e mantém o código existente intacto.

### 2. Remoção do Spotify

O módulo é 100% dependente de AppleScript (`osascript`), inútil no Linux. Removemos:
pacote `zaya/spotify/`, `tests/test_spotify.py`, subparser `zaya spotify`, handler em
`turns.py` (`_handle_spotify`, intent parsing em `_dispatch`/`_dispatch_silent`), função
`_spotify_command` em `cli_integrations.py` e as menções no README. Não há config nem
env var de Spotify — confirmado por grep.

### 3. Serviço systemd em vez de launchd

O comentário em `zaya/telegram/service.py` menciona launchd; no Linux o equivalente é uma
unit de usuário. `Restart=on-failure` + `RestartSec=10`. `systemctl --user enable --now`
sobe no login. O backoff já existente no long polling (até 30s) cobre indisponibilidade
da API do Telegram.

### 4. Login do navegador em um único passo, headless-friendly

A `BrowserSession` já suporta perfil persistente e o comando `zaya browser login <url>`
já abre o navegador headed e espera Enter para salvar. Num mini headless, damos uma tela
virtual só durante o login:

- `scripts/browser-login.sh <url>` → sobe `Xvfb :99` + `x11vnc` → roda
  `DISPLAY=:99 zaya browser login <url>` → ao sair, derruba os dois processos.
- A usuária conecta pelo Finder do Mac (`vnc://<ip-do-mini>:5900`), loga, volta ao SSH e
  pressiona Enter. O perfil é salvo e o serviço passa a rodar headless com esses logins.
- Dependências: `xvfb` e `x11vnc` (apt), instaladas à parte, não no `install-linux.sh`
  padrão (YAGNI para quem não usa navegador logado).

### 5. Permissões do bot

`zaya telegram serve` roda com `TerminalUI(quiet=True)`, então `write`/`edit`/`bash` só
funcionam com `ZAYA_TELEGRAM_AUTO_APPROVE=true` **e** `ZAYA_TELEGRAM_ALLOWED_CHAT_ID`
fixado (a política já existe em `telegram_permission`). O chat_id fixo impede que o bot
adote o primeiro desconhecido que falar com ele.

## Fluxo de entrega proativa

```
_proactive_loop / _fire_due_reminders / _check_focus / _check_agenda
        └─ _say_proactive(text)
              ├─ ui.proactive(text)          (sempre: log/registro)
              ├─ channel == "telegram" → telegram.send_message(text)  [sem TTS]
              └─ channel == "cli"      → _speak(text)                 [TTS local]
```

`briefing.py` e `career.py` já enviam direto pelo Telegram e não mudam.

## Riscos

| Risco | Mitigação |
|---|---|
| Sem saída de áudio no mini, `_speak` falharia | canal Telegram não chama `_speak` |
| `edge-tts` depende de internet | texto sempre é enviado; voz é opcional |
| Chromium no Ubuntu sem libs do sistema | `playwright install-deps chromium` no install |
| Bot sequestrado por terceiro | `allowed_chat_id` obrigatório junto do auto-approve |
| Perfil do navegador corrompido entre versões | recriar com `browser-login.sh` |

## Validação

- Testes unitários novos: entrega proativa no Telegram, remoção do Spotify (imports
  ausentes), ausência de TTS no canal Telegram.
- No mini: `zaya doctor`, mensagem de texto e áudio no Telegram, lembrete disparado
  chegando no Telegram, `browser-login.sh` uma vez e depois `zaya browser text` headless.
- Reinício da máquina: `systemctl --user status zaya-telegram` deve voltar sozinho.
