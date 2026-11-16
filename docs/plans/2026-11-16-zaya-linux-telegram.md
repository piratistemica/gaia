# Zaya no Ubuntu (headless) + Telegram Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rodar a Zaya num mini PC Ubuntu sem tela, controlada do MacBook pelo Telegram, com navegador com logins salvos e proatividade entregue no chat.

**Architecture:** O `Orchestrator` já carrega `channel` (`cli` | `telegram`). Usamos esse sinal para decidir a entrega da fala: no canal `telegram` as mensagens proativas vão por `TelegramClient.send_message` e o TTS local é desligado; no canal `cli` nada muda. O processo roda como unit systemd de usuário. Spotify (AppleScript-only) é removido por inteiro.

**Tech Stack:** Python 3.12, pytest, httpx, Playwright (Chromium), Xvfb/x11vnc (login único), systemd, uv.

**Design de referência:** `docs/plans/2026-11-16-zaya-linux-telegram-design.md`

---

## Antes de começar

```bash
cd /Users/talita/zaya
git status --short          # há mudanças não commitadas de outro trabalho
git checkout -b feat/linux-headless-telegram
pytest -q                   # linha de base deve passar
```

Regra: **um commit por tarefa**, só com os arquivos daquela tarefa.

---

### Task 1: Remover o Spotify do orquestrador e do CLI

**Files:**
- Delete: `zaya/spotify/__init__.py`, `zaya/spotify/controller.py`, `zaya/spotify/intents.py`
- Delete: `tests/test_spotify.py`
- Modify: `zaya/controller/turns.py:42-44`, `turns.py:74-76`, `turns.py:95-97`, `turns.py:131-139`
- Modify: `zaya/cli_parser.py:90-96`
- Modify: `zaya/cli.py:16-22`, `cli.py:60-61`
- Modify: `zaya/cli_integrations.py:1`, `cli_integrations.py:12-13`, `cli_integrations.py:137-157`

**Step 1: Apagar o pacote e o teste**

```bash
rm -rf zaya/spotify tests/test_spotify.py
```

**Step 2: Remover os imports e os handlers do orquestrador**

Em `zaya/controller/turns.py`, apagar:

```python
from zaya.spotify.controller import SpotifyCommand, SpotifyError
from zaya.spotify.controller import execute as execute_spotify
from zaya.spotify.intents import parse_spotify_command
```

Em `_dispatch_silent`, apagar:

```python
        spotify = parse_spotify_command(text)
        if spotify is not None:
            return await self._handle_spotify(spotify, speak=False)
```

Em `_dispatch`, apagar:

```python
        spotify = parse_spotify_command(text)
        if spotify is not None:
            return await self._handle_spotify(spotify, speak=True)
```

E apagar o método inteiro:

```python
    async def _handle_spotify(self, command: SpotifyCommand, speak: bool) -> str:
        try:
            message = await asyncio.to_thread(execute_spotify, command)
        except SpotifyError as exc:
            message = f"Spotify: {exc}"
        self.ui.info(message)
        if speak:
            await self._speak(message)
        return message
```

**Step 3: Remover o subparser**

Em `zaya/cli_parser.py`, apagar o bloco:

```python
    spotify_parser = sub.add_parser("spotify", help="controla o Spotify no macOS")
    spotify_sub = spotify_parser.add_subparsers(dest="spotify_command")
    for name in ("status", "toggle", "play", "pause", "next", "prev"):
        spotify_sub.add_parser(name)
    spotify_volume = spotify_sub.add_parser("volume", help="define o volume (0-100)")
    spotify_volume.add_argument("value", type=int)
```

**Step 4: Remover do CLI**

Em `zaya/cli.py`, remover `_spotify_command,` do import de `zaya.cli_integrations` e o branch:

```python
        elif args.command == "spotify":
            _spotify_command(args, ui)
```

**Step 5: Remover de `cli_integrations.py`**

- Docstring linha 1 vira: `"""Comandos de integração: WhatsApp, Telegram e navegador."""`
- Apagar os dois imports de `zaya.spotify...`
- Apagar `def _spotify_command(args, ui: TerminalUI) -> None:` e todo o corpo.

**Step 6: Garantir que não sobrou referência**

Run: `grep -rn "spotify\|Spotify" zaya tests --include="*.py"`
Expected: nenhuma saída.

**Step 7: Rodar a suíte**

Run: `pytest -q`
Expected: PASS (sem `test_spotify.py`, nada mais quebrado).

**Step 8: Conferir o help do CLI**

Run: `.venv/bin/python -m zaya --help`
Expected: não aparece mais `spotify`; seguem `telegram`, `browser`, `career`, `whatsapp`.

**Step 9: Commit**

```bash
git add -A zaya/spotify tests/test_spotify.py zaya/controller/turns.py zaya/cli_parser.py zaya/cli.py zaya/cli_integrations.py
git commit -m "refactor: remove o controle de Spotify (AppleScript, só macOS)"
```

---

### Task 2: Entregar mensagens proativas pelo Telegram

**Files:**
- Modify: `zaya/controller/initiative.py:246-251`
- Test: `tests/test_orchestrator.py` (adicionar ao final)

**Step 1: Escrever os testes que falham**

Adicionar ao final de `tests/test_orchestrator.py`:

```python
class FakeTelegram:
    enabled = True

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send_message(self, text: str, chat_id=None):
        if self.fail:
            raise RuntimeError("telegram fora do ar")
        self.sent.append(text)
        return {"message_id": len(self.sent)}


@pytest.mark.asyncio
async def test_proativo_no_canal_telegram_vai_para_o_chat(orch):
    telegram = FakeTelegram()
    orch.telegram = telegram
    orch.channel = "telegram"

    await orch._say_proactive("Lembrete: beber água")

    assert telegram.sent == ["Lembrete: beber água"]
    assert orch.tts.spoken == []


@pytest.mark.asyncio
async def test_proativo_no_canal_cli_continua_falando_local(orch):
    telegram = FakeTelegram()
    orch.telegram = telegram
    orch.channel = "cli"

    await orch._say_proactive("Bom dia")

    assert telegram.sent == []
    assert orch.tts.spoken == ["Bom dia"]


@pytest.mark.asyncio
async def test_proativo_nao_quebra_quando_o_telegram_falha(orch, caplog):
    orch.telegram = FakeTelegram(fail=True)
    orch.channel = "telegram"

    await orch._say_proactive("qualquer coisa")

    assert orch.ui.called("proactive")
```

**Step 2: Rodar e ver falhar**

Run: `pytest tests/test_orchestrator.py -k proativo -v`
Expected: FAIL — as mensagens não chegam em `telegram.sent` (o `_say_proactive` atual só chama `_speak`).

**Step 3: Implementar**

Em `zaya/controller/initiative.py`, substituir `_say_proactive` por:

```python
    async def _say_proactive(self, text: str, count: bool = True) -> None:
        self.ui.proactive(text)
        if count:
            self._proactive_count += 1
            self._last_proactive = time.monotonic()
        if self.channel == "telegram":
            await self._notify_telegram(text)
            return
        await self._speak(text)

    async def _notify_telegram(self, text: str) -> None:
        """Entrega proativa no chat. Falha de rede nao pode matar o laco."""
        if not getattr(self.telegram, "enabled", False):
            return
        try:
            await self.telegram.send_message(text)
        except Exception as exc:
            self.logger.warning("proativo: falha ao enviar no Telegram: %s", exc)
```

**Step 4: Rodar e ver passar**

Run: `pytest tests/test_orchestrator.py -k proativo -v`
Expected: PASS (3 testes).

**Step 5: Rodar a suíte inteira**

Run: `pytest -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add zaya/controller/initiative.py tests/test_orchestrator.py
git commit -m "feat(proativo): entrega lembretes e avisos pelo Telegram quando o canal é telegram"
```

---

### Task 3: Desligar o TTS local no canal Telegram

Sem isso, num mini sem placa de som, `_speak` chamaria `ffplay` e falharia (ou travaria)
a cada lembrete confirmado.

**Files:**
- Modify: `zaya/controller/speech.py:199-201`
- Test: `tests/test_orchestrator.py` (adicionar ao final)

**Step 1: Escrever o teste que falha**

```python
@pytest.mark.asyncio
async def test_speak_nao_toca_audio_local_no_canal_telegram(orch):
    orch.channel = "telegram"

    await orch._speak("Combinado. Vou te lembrar.")

    assert orch.tts.spoken == []
```

**Step 2: Rodar e ver falhar**

Run: `pytest tests/test_orchestrator.py::test_speak_nao_toca_audio_local_no_canal_telegram -v`
Expected: FAIL — `orch.tts.spoken == ["Combinado. Vou te lembrar."]`.

**Step 3: Implementar o guard**

Em `zaya/controller/speech.py`, no começo de `_speak`:

```python
    async def _speak(self, text: str) -> None:
        # No canal Telegram a voz já é feita pelo bot (voice_replies) e a
        # máquina pode não ter saída de áudio: não tentar tocar localmente.
        if self.channel == "telegram":
            return
        if not self.tts.enabled:
            return
```

**Step 4: Rodar e ver passar**

Run: `pytest tests/test_orchestrator.py -k speak -v`
Expected: PASS.

**Step 5: Suíte completa**

Run: `pytest -q`
Expected: PASS. Se algum teste antigo assumia fala no canal telegram, ajustar o teste
(não o código) explicando no commit.

**Step 6: Commit**

```bash
git add zaya/controller/speech.py tests/test_orchestrator.py
git commit -m "fix(telegram): não tenta tocar TTS local no canal do bot"
```

---

### Task 4: Instalador com Playwright e flag de navegador

**Files:**
- Modify: `scripts/install-linux.sh`

**Step 1: Adicionar a flag**

No parsing de argumentos, adicionar `--with-browser`:

```bash
SKIP_APT=0
SKIP_LAUNCHER=0
WITH_BROWSER=0

for arg in "$@"; do
  case "$arg" in
    --no-apt) SKIP_APT=1 ;;
    --no-launcher) SKIP_LAUNCHER=1 ;;
    --with-browser) WITH_BROWSER=1 ;;
    ...
```

**Step 2: Instalar o Chromium e as libs do sistema**

Logo depois do bloco do venv (`uv pip install --python .venv/bin/python -e .`), inserir:

```bash
if [ "$WITH_BROWSER" -eq 1 ]; then
  say "Instalando o navegador (Playwright + Chromium)…"
  uv pip install --python .venv/bin/python -e ".[browser]"
  if [ "$SKIP_APT" -eq 0 ] && command -v apt-get >/dev/null 2>&1; then
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      xvfb x11vnc || warn "xvfb/x11vnc falharam; o login gráfico do navegador não vai funcionar."
  fi
  .venv/bin/playwright install chromium
  .venv/bin/playwright install-deps chromium \
    || warn "install-deps falhou; se o Chromium não abrir, rode com sudo."
fi
```

**Step 3: Atualizar o cabeçalho de uso do script**

Nas linhas 2–12 (comentário de uso), acrescentar:

```bash
#   ./scripts/install-linux.sh --with-browser   # + Playwright, Chromium, Xvfb, x11vnc
```

**Step 4: Validar sintaxe**

Run: `bash -n scripts/install-linux.sh`
Expected: sem saída (sintaxe ok).

**Step 5: Validar o help**

Run: `./scripts/install-linux.sh --help`
Expected: mostra as três flags incluindo `--with-browser`.

**Step 6: Commit**

```bash
git add scripts/install-linux.sh
git commit -m "feat(linux): instalador com Playwright, Chromium, Xvfb e x11vnc opcionais"
```

---

### Task 5: Login do navegador numa tela virtual (VNC único)

**Files:**
- Create: `scripts/browser-login.sh`

**Step 1: Criar o script**

`scripts/browser-login.sh`:

```bash
#!/usr/bin/env bash
# Login persistente no navegador da Zaya dentro de um mini PC headless.
#
#   ./scripts/browser-login.sh https://exemplo.com
#
# Sobe um display virtual (Xvfb) e um servidor VNC (x11vnc) apontando para ele,
# roda `zaya browser login <url>` nesse display e, ao sair, derruba tudo.
# Conecte do Mac com: Finder > Ir > Conectar ao Servidor > vnc://IP-DO-MINI:5900

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZAYA_BIN="$REPO_DIR/.venv/bin/zaya"
URL="${1:-}"
DISPLAY_NUM="${ZAYA_VNC_DISPLAY:-:99}"
VNC_PORT="${ZAYA_VNC_PORT:-5900}"

if [ -z "$URL" ]; then
  echo "uso: $0 <url>" >&2
  exit 2
fi
[ -x "$ZAYA_BIN" ] || { echo "rode scripts/install-linux.sh --with-browser antes" >&2; exit 1; }
command -v Xvfb >/dev/null || { echo "falta Xvfb (sudo apt install xvfb)" >&2; exit 1; }
command -v x11vnc >/dev/null || { echo "falta x11vnc (sudo apt install x11vnc)" >&2; exit 1; }

XVFB_PID=""
VNC_PID=""
cleanup() {
  [ -n "$VNC_PID" ] && kill "$VNC_PID" 2>/dev/null || true
  [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "subindo display virtual $DISPLAY_NUM…"
Xvfb "$DISPLAY_NUM" -screen 0 1280x900x24 >/dev/null 2>&1 &
XVFB_PID=$!
sleep 1

echo "subindo VNC na porta $VNC_PORT…"
x11vnc -display "$DISPLAY_NUM" -rfbport "$VNC_PORT" -localhost -forever -shared -quiet >/dev/null 2>&1 &
VNC_PID=$!
sleep 1

cat <<EOF
Conecte do Mac em: vnc://$(hostname -I | awk '{print $1}'):$VNC_PORT
(se o mini estiver fora da sua rede, use o IP do Tailscale)
EOF

DISPLAY="$DISPLAY_NUM" "$ZAYA_BIN" browser login "$URL" || true
echo "sessão salva no perfil persistente do navegador."
```

**Step 2: Dar permissão de execução**

```bash
chmod +x scripts/browser-login.sh
```

**Step 3: Validar sintaxe e guardas**

Run: `bash -n scripts/browser-login.sh && ./scripts/browser-login.sh`
Expected: `bash -n` sem saída; o script sai com código 2 e imprime
`uso: ./scripts/browser-login.sh <url>`.

**Step 4: Commit**

```bash
git add scripts/browser-login.sh
git commit -m "feat(linux): login do navegador via Xvfb + x11vnc para mini PC headless"
```

---

### Task 6: Unit systemd para o serviço do Telegram

**Files:**
- Create: `scripts/install-systemd.sh`
- Create: `systemd/zaya-telegram.service.in`

**Step 1: Criar o template da unit**

`systemd/zaya-telegram.service.in`:

```ini
[Unit]
Description=Zaya (serviço do Telegram)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=__REPO_DIR__
ExecStart=__REPO_DIR__/.venv/bin/zaya telegram serve
Restart=on-failure
RestartSec=10
# Nao deixar o servico morrer por falta de audio/video.
Environment=PYTHONUNBUFFERED=1
# Logs vao para o journal: journalctl --user -u zaya-telegram -f

[Install]
WantedBy=default.target
```

**Step 2: Criar o instalador**

`scripts/install-systemd.sh`:

```bash
#!/usr/bin/env bash
# Instala o servico de usuario que mantem `zaya telegram serve` vivo no boot.
#
#   ./scripts/install-systemd.sh            # instala e sobe
#   ./scripts/install-systemd.sh --remove   # desinstala

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/zaya-telegram.service"

if [ "${1:-}" = "--remove" ]; then
  systemctl --user disable --now zaya-telegram.service 2>/dev/null || true
  rm -f "$UNIT"
  systemctl --user daemon-reload
  echo "serviço removido."
  exit 0
fi

[ -x "$REPO_DIR/.venv/bin/zaya" ] || { echo "instale a Zaya antes (scripts/install-linux.sh)" >&2; exit 1; }

mkdir -p "$UNIT_DIR"
sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/systemd/zaya-telegram.service.in" > "$UNIT"

systemctl --user daemon-reload
systemctl --user enable --now zaya-telegram.service

echo "serviço ativo. acompanhe com: journalctl --user -u zaya-telegram -f"
echo "para sobreviver sem sessão aberta: sudo loginctl enable-linger $USER"
```

**Step 3: Executar permissões**

```bash
chmod +x scripts/install-systemd.sh
```

**Step 4: Validar sem instalar de verdade**

Run: `bash -n scripts/install-systemd.sh && sed "s|__REPO_DIR__|/tmp/x|g" systemd/zaya-telegram.service.in | grep -c "/tmp/x"`
Expected: `bash -n` sem saída; o grep conta `2` (WorkingDirectory e ExecStart).

**Step 5: Commit**

```bash
git add systemd/zaya-telegram.service.in scripts/install-systemd.sh
git commit -m "feat(linux): unit systemd de usuário para manter o Telegram rodando"
```

---

### Task 7: Documentação de configuração e operação

**Files:**
- Modify: `.env.example:133-149`
- Modify: `README.md:90-100`, `README.md:337`, adicionar seção "No Ubuntu (headless)"

**Step 1: Atualizar o `.env.example`**

Na seção do Telegram, explicitar o par obrigatório para editar arquivos pelo bot:

```bash
# Aprovar write/edit/bash automaticamente (o Telegram não tem terminal para
# pedir permissão; exige ZAYA_TELEGRAM_ALLOWED_CHAT_ID fixado):
# ZAYA_TELEGRAM_AUTO_APPROVE=false
# Em mini PC headless recomenda-se:
#   ZAYA_TELEGRAM_ENABLED=true
#   ZAYA_TELEGRAM_ALLOWED_CHAT_ID=<seu chat_id>   # fixo, nunca vazio
#   ZAYA_TELEGRAM_AUTO_APPROVE=true
#   ZAYA_TELEGRAM_VOICE_REPLIES=true              # resposta também em áudio
```

**Step 2: Remover a menção ao Spotify do README**

Apagar a linha 337 (`**No Spotify** — ...`) e qualquer outra menção a Spotify:

Run: `grep -n -i spotify README.md`
Expected: nenhuma saída.

**Step 3: Adicionar a seção de operação no Ubuntu**

Depois da seção "Onde ela aparece", inserir:

```markdown
## No Ubuntu (mini PC headless)

O caminho recomendado para uma máquina sem tela é o Telegram como canal principal.

```bash
git clone <repo> ~/zaya && cd ~/zaya
./scripts/install-linux.sh --with-browser
./scripts/install-systemd.sh          # mantém `zaya telegram serve` vivo no boot

# login único nos sites, numa tela virtual acessível por VNC:
./scripts/browser-login.sh https://exemplo.com
```

Conecte do Mac pelo Finder (`vnc://IP-DO-MINI:5900`), faça login e volte ao SSH para
pressionar Enter. O perfil fica salvo e a Zaya passa a usar o navegador headless com esses
logins.

**Avisos proativos** (bom dia, lembretes, agenda) são entregues no chat do Telegram
quando ela roda como serviço. Manutenção: `ssh`/VNC; logs: `journalctl --user -u zaya-telegram -f`.
```

**Step 4: Conferir o README**

Run: `grep -n "Ubuntu (mini PC headless)" README.md && grep -c "install-systemd" README.md`
Expected: a seção existe e a contagem é ≥ 1.

**Step 5: Commit**

```bash
git add .env.example README.md
git commit -m "docs: operação no Ubuntu headless com Telegram, navegador e systemd"
```

---

### Task 8: Verificação final

**Step 1: Suíte e linters**

```bash
pytest -q
.venv/bin/ruff check zaya tests
.venv/bin/mypy
```
Expected: tudo passa (ou apenas avisos já existentes antes da mudança).

**Step 2: Nenhum resíduo de Spotify**

```bash
grep -rn -i "spotify" zaya tests README.md .env.example scripts systemd || echo "limpo"
```
Expected: `limpo`.

**Step 3: Smoke test do CLI**

```bash
.venv/bin/python -m zaya --help
.venv/bin/zaya doctor
```
Expected: `--help` sem `spotify`, com `telegram`/`browser`; `doctor` mostra o provedor de
TTS como `edge`.

**Step 4: Teste real no mini PC (manual, na máquina)**

1. `./scripts/install-linux.sh --with-browser`
2. Configurar `~/.config/zaya/.env` com token, `ALLOWED_CHAT_ID`, `AUTO_APPROVE=true`.
3. `zaya telegram me` → confirma o bot.
4. `./scripts/install-systemd.sh` → `systemctl --user status zaya-telegram`.
5. Do Mac: mandar texto, mandar áudio, pedir "me lembra de X em 2 minutos".
   - Deve responder no chat; o lembrete deve **chegar no chat**.
6. `./scripts/browser-login.sh https://github.com` pelo VNC, logar, Enter.
7. `zaya browser text https://github.com` → deve trazer a página já autenticada.
8. `sudo reboot` → após o boot, mandar mensagem no Telegram sem tocar no mini.

**Step 5: Commit final (se houver ajustes)**

```bash
git add -A
git commit -m "chore: ajustes finais do modo headless no Ubuntu"
```

---

## Notas de execução

- Não commitar `agentes-chat-export.json` nem os arquivos fora do escopo de cada tarefa.
- `briefing.py` e `career.py` já enviam pelo Telegram; não mexer além do necessário.
- Se um teste antigo depender do TTS no canal telegram, ajustar o teste e justificar no
  corpo do commit.
