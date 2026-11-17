# zmin (kit de administração do mini) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transformar o `zmin` de "atalhos de SSH" em um kit que protege o estado do mini (backup/restore), verifica a segurança de forma repetível (audit), conserta sozinho o que é seguro (fix) e faz deploy com rede de proteção (teste + rollback).

**Architecture:** Duas metades com donos claros. `scripts/zmin.sh` roda **no MacBook** e cuida de orquestração, estado local (arquivos de backup, cifra, retenção) e SSH. `scripts/zmin-remote.sh` roda **no mini** e é a única parte que toca em `/etc`, em sqlite e no systemd do mini — assim o Mac nunca precisa de quoting exótico dentro de `ssh` (foi exatamente essa classe de bug que quebrou o `sed` do `hardening-ssh.sh` na mão). Ambos são bash puro (o MacBook tem **bash 3.2**, sem arrays associativos) e os testes são um `zmin-selftest.sh` sem dependências, mais um modo `ZMIN_HOST=local` que executa o lado remoto na própria máquina, permitindo backup/restore de verdade contra um `HOME` temporário.

**Tech Stack:** bash 3.2 (macOS) / bash 5 (Ubuntu), `rsync`, `tar`, `openssl` (cifra), `ssh`, `launchd` (vigia), sqlite via `python3` do venv do mini (`~/.venv/bin/python`, sqlite 3.53.1).

**Design de referência:** `docs/acesso-mini.md` (o que existe hoje) e a conversa de 2026-11-16 sobre os 4 riscos de segurança.

---

## Antes de começar

```bash
cd /Users/talita/zaya
git status --short        # 14 arquivos modificados de outro trabalho (agenda tools, shell_access)
git checkout -b feat/zmin-admin
.venv/bin/python -m pytest -q   # linha de base: 407 passed
.venv/bin/ruff check .          # linha de base: All checks passed
```

Regras do plano:

1. **um commit por tarefa**, só com os arquivos daquela tarefa;
2. `scripts/zmin-selftest.sh` roda a cada tarefa que mexe em lógica; se ele não passar, não commita;
3. nada destrutivo sem `--yes` explícito;
4. o lado remoto nunca recebe comando montado por concatenação de string: sempre `ssh "$HOST" bash -s <<'REMOTE'` ou argumento único já citado.

## Decisões já tomadas (e por quê)

| decisão | motivo |
|---|---|
| `ZMIN_HOST=local` executa o lado remoto localmente | dá para testar backup/restore de verdade (contra `$HOME` temporário) sem depender do mini nem sujar dados reais |
| backup usa `.backup` do sqlite (via python do venv) em vez de copiar `zaya.db` | copiar o arquivo com o serviço rodando pega `-wal`/`-shm` no meio: o snapshot do sqlite é consistente por construção |
| `sb_index.db` **não** entra no backup | é derivável com `zaya index --force`; evita carregar 400 KB de índice que muda toda hora |
| cifra com `openssl enc -aes-256-cbc -pbkdf2` | `age`/`gpg` não existem no MacBook; `openssl` existe nos dois. Frase-senha **nunca** fica em arquivo |
| retenção de 10 arquivos | 5 MB por backup: 10 = 50 MB, irrelevante no disco do MacBook |
| `zmin` continua sendo um arquivo bash único do lado Mac | precisa funcionar quando o Python do repo está quebrado — é a ferramenta de resgate |
| audit sai com código de saída 0/1/2 | permite usar em script/launchd sem parsear texto |

## O que existe hoje (ponto de partida)

`scripts/zmin.sh` (6731 bytes): `doctor`, `logs`, `restart`, `deploy`, `test`, `agenda`, `ask`, `shell`, `sudo`. Sem backup, sem restore, sem audit, sem fix, sem rollback. Estado do mini não tem backup nenhum:

```
~/.config/zaya        36 KB   config.toml + .env (token do bot) + agenda.env (chave-mestra)
~/.local/share/zaya  2,4 MB   zaya.db (472 KB), sessions (956 KB), career/
~/SecondBrain        2,4 MB   notas markdown
```

---

# Fase 1 — Backup e restore (o que dói hoje)

### Task 1: Harness de teste do zmin

**Files:**
- Modify: `scripts/zmin.sh:1-30` (cabeçalho e guarda do `main`)
- Create: `scripts/zmin-selftest.sh`

**Step 1: Escrever o selftest que falha**

`scripts/zmin-selftest.sh`:

```bash
#!/usr/bin/env bash
# Testa as funções puras do zmin sem dependência externa (bash 3.2 ok).
set -uo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ZMIN_LIB_ONLY=1 . "$DIR/zmin.sh"

falhas=0
caso() { printf '  %s\n' "$1"; }
ok()   { printf '    ok: %s\n' "$1"; }
erro() { printf '    FALHOU: %s\n' "$1" >&2; falhas=$((falhas + 1)); }
igual() { if [ "$2" = "$3" ]; then ok "$1"; else erro "$1 (esperado: [$3], veio: [$2])"; fi; }

caso "citado: argumento com espaço sobrevive"
igual "sed com espaço" "$(_citado "s/a b/c d/" /etc/x)" " 's/a b/c d/' /etc/x"
caso "citado: aspas simples no meio"
igual "aspas" "$(_citado "a'b")" " 'a'\''b'"
caso "retencao poda os mais antigos"
tmp=$(mktemp -d)
for n in 1 2 3 4 5; do : >"$tmp/mini-2026010${n}-000000.tar.gz"; done
_podar "$tmp" "mini-*.tar.gz" 3
igual "sobram 3" "$(ls "$tmp" | wc -l | tr -d ' ')" "3"
igual "os mais antigos sairam" "$(ls "$tmp" | head -1)" "mini-20260103-000000.tar.gz"
rm -rf "$tmp"
caso "nome de backup tem data e host"
igual "formato" "$(_nome_backup zaya)" "$(date +%Y%m%d)-000000-zaya.tar.gz"   # só o formato é checado abaixo
printf '%s\n' "$(_nome_backup zaya)" | grep -qE '^[0-9]{8}-[0-9]{6}-zaya\.tar\.gz$' \
  && ok "regex do nome" || erro "regex do nome"

if [ "$falhas" -ne 0 ]; then
  printf '\n%d falha(s)\n' "$falhas" >&2
  exit 1
fi
printf '\nselftest ok\n'
```

**Step 2: Rodar e ver falhar**

Run: `bash scripts/zmin-selftest.sh`
Expected: erro imediato `zmin.sh: linha N: ZMIN_LIB_ONLY: unbound variable` ou `_citado: command not found` — as três funções ainda não existem.

**Step 3: Implementar as funções puras em `scripts/zmin.sh`**

Logo depois das variáveis de configuração:

```bash
# ---------------------------------------------------------------- utilidades
_citado() {          # cita cada argumento para atravessar o ssh inteiro
  local saida="" arg
  for arg in "$@"; do saida+=" $(printf '%q' "$arg")"; done
  printf '%s' "$saida"
}

_nome_backup() {     # $1 = host; nome ordenável por data
  printf '%s-%s-%s.tar.gz' "$(date +%Y%m%d)" "$(date +%H%M%S)" "$1"
}

_podar() {           # $1 = dir, $2 = glob, $3 = quantos manter
  local dir="$1" glob="$2" manter="${3:-10}" lista total
  lista=$(ls -1t "$dir"/$glob 2>/dev/null || true)
  total=$(printf '%s\n' "$lista" | grep -c . || true)
  [ "$total" -le "$manter" ] && return 0
  printf '%s\n' "$lista" | tail -n +$((manter + 1)) | while read -r velho; do
    [ -n "$velho" ] && rm -f "$velho"
  done
}
```

E no fim do arquivo, trocar `main "$@"` por:

```bash
if [ "${ZMIN_LIB_ONLY:-0}" != "1" ]; then
  main "$@"
fi
```

**Step 4: Rodar o selftest**

Run: `bash scripts/zmin-selftest.sh`
Expected: `selftest ok` (o caso "formato" pode falhar por comparar com hora fixa — deixe só o regex como asserção real).

**Step 5: Commit**

```bash
git add scripts/zmin.sh scripts/zmin-selftest.sh
git commit -m "test(zmin): selftest sem dependências para as funções puras"
```

---

### Task 2: Lado remoto — empacotar o estado do mini

**Files:**
- Create: `scripts/zmin-remote.sh`

**Step 1: Escrever o script remoto (com `--dry-run` para ser testável)**

`scripts/zmin-remote.sh`:

```bash
#!/usr/bin/env bash
# Roda NO MINI. Nunca é chamado direto: `zmin` envia por ssh (ou localmente,
# quando ZMIN_HOST=local, para o selftest).
#
#   zmin-remote.sh backup [--dry-run]   → tar.gz do estado no stdout
#   zmin-remote.sh info                 → fatos em KEY=VALUE (usado pelo audit)
set -euo pipefail

HOME_DIR="${HOME}"
VENV_PY="$HOME_DIR/zaya/.venv/bin/python"
AREAS=(.config/zaya .local/share/zaya SecondBrain)
# Índice do Second Brain é derivável (`zaya index --force`): fica fora.
EXCLUIR=(--exclude 'sb_index.db' --exclude 'sb_index.db-wal' --exclude 'sb_index.db-shm'
         --exclude '*.log' --exclude 'tmp/' --exclude 'browser/')

_sqlite_consistente() {   # $1 = origem, $2 = destino
  "$VENV_PY" - "$1" "$2" <<'PY'
import sqlite3, sys
origem, destino = sys.argv[1], sys.argv[2]
with sqlite3.connect(origem) as src, sqlite3.connect(destino) as dst:
    src.backup(dst)
PY
}

cmd_backup() {
  local dry=0
  [ "${1:-}" = "--dry-run" ] && dry=1
  local staging
  staging=$(mktemp -d "${TMPDIR:-/tmp}/zmin-backup-XXXXXX")
  trap 'rm -rf "$staging"' EXIT

  for area in "${AREAS[@]}"; do
    [ -d "$HOME_DIR/$area" ] || continue
    mkdir -p "$staging/$(dirname "$area")"
    cp -a "$HOME_DIR/$area" "$staging/$area"
  done
  # dbs copiados de forma consistente (o cp -a acima pegou -wal/-shm sujos)
  for db in zaya.db; do
    alvo="$staging/.local/share/zaya/$db"
    [ -f "$alvo" ] || continue
    rm -f "$alvo"-wal "$alvo"-shm
    _sqlite_consistente "$HOME_DIR/.local/share/zaya/$db" "$alvo"
  done

  if [ "$dry" -eq 1 ]; then
    echo "DRY-RUN: empacotaria:"
    ( cd "$staging" && find . -type f | sort )
    du -sh "$staging"
    return 0
  fi
  tar czf - -C "$staging" . "${EXCLUIR[@]:-}" 2>/dev/null || tar czf - -C "$staging" .
}

cmd_info() {
  printf 'HOME=%s\n' "$HOME_DIR"
  printf 'OS=%s\n' "$(uname -sr)"
  printf 'DISK_PCT=%s\n' "$(df -P / | awk 'NR==2{gsub("%","",$5); print $5}')"
  printf 'DISK_FREE=%s\n' "$(df -h / | awk 'NR==2{print $4}')"
  printf 'CRYPT=%s\n' "$(lsblk -o TYPE 2>/dev/null | grep -c crypt || echo 0)"
  printf 'UFW_ACTIVE=%s\n' "$(systemctl is-active ufw 2>/dev/null || echo inactive)"
  printf 'SSHD_PASSWORD_AUTH=%s\n' \
    "$(grep -qiE '^[[:space:]]*PasswordAuthentication[[:space:]]+yes' /etc/ssh/sshd_config && echo yes || echo no)"
  printf 'SERVICE=%s\n' "$(systemctl --user is-active zaya-telegram.service 2>/dev/null || echo inactive)"
}

case "${1:-}" in
  backup) shift; cmd_backup "${1:-}" ;;
  info) cmd_info ;;
  *) echo "uso: zmin-remote.sh backup [--dry-run] | info" >&2; exit 2 ;;
esac
```

**Step 2: Testar o lado remoto sem tocar no mini real**

O script é idempotente e só lê; rode direto:

Run: `scp -q scripts/zmin-remote.sh zaya:/tmp/ && ssh zaya 'bash /tmp/zmin-remote.sh info'`
Expected:

```
HOME=/home/<usuario>
OS=Linux 7.0.0-30-generic
DISK_PCT=25
...
SERVICE=active
```

Run: `ssh zaya 'bash /tmp/zmin-remote.sh backup --dry-run | tail -5'`
Expected: lista de arquivos terminando em `SecondBrain/...` e um total perto de `5,0M`.

**Step 3: Conferir que o tar abre e tem o essencial**

Run:
```bash
ssh zaya 'bash /tmp/zmin-remote.sh backup' > /tmp/teste-backup.tar.gz
tar tzf /tmp/teste-backup.tar.gz | grep -E 'agenda\.env|zaya\.db$|config\.toml$' | sort
```
Expected: as três linhas presentes; **nenhuma** linha com `sb_index.db`.

**Step 4: Commit**

```bash
git add scripts/zmin-remote.sh
git commit -m "feat(zmin): helper remoto com backup consistente dos sqlite e info do host"
```

---

### Task 3: `zmin backup` (com retenção, checksum e cifra opcional)

**Files:**
- Modify: `scripts/zmin.sh` (nova função `cmd_backup`, dispatch em `main`)
- Modify: `scripts/zmin-selftest.sh` (caso adicional)

**Step 1: Escrever a função**

```bash
cmd_backup() {
  local destino="$HOME/zaya-backups/mini" manter=10 cifrar=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --out) destino="$2"; shift 2 ;;
      --keep) manter="$2"; shift 2 ;;
      --encrypt) cifrar=1; shift ;;
      *) die "backup: opção desconhecida: $1" ;;
    esac
  done
  mkdir -p "$destino"
  local nome arquivo
  nome=$(_nome_backup "$HOST")
  arquivo="$destino/$nome"

  info "empacotando o estado do mini ($(ssh_remote 'du -sh ~ 2>/dev/null | cut -f1') total, ~5 MB útil)"
  ssh_remote_raw >"$arquivo.tmp" || { rm -f "$arquivo.tmp"; die "backup falhou no ssh"; }
  [ -s "$arquivo.tmp" ] || { rm -f "$arquivo.tmp"; die "arquivo vazio — abortando"; }
  mv "$arquivo.tmp" "$arquivo"
  chmod 600 "$arquivo"

  if [ "$cifrar" -eq 1 ]; then
    printf 'frase-senha do backup (não fica salva em lugar nenhum): '
    stty -echo; IFS= read -r senha; stty echo; printf '\n'
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
      -in "$arquivo" -out "$arquivo.enc" -pass env:ZMIN_PASS
    …
  fi
  …
}
```

> **Nota de implementação:** `-pass env:` exige a variável exportada no ambiente do processo; use
> `ZMIN_PASS="$senha" openssl enc … -pass env:ZMIN_PASS` (não passa pela linha de comando, não
> aparece em `ps`). Depois `shasum -a 256` do arquivo final e `_podar`.

Funções de apoio já necessárias:

```bash
ssh_remote()      { "${SSH[@]}" "$@"; }                                   # comando já citado
ssh_remote_raw()  { "${SSH[@]}" "bash $REMOTE_REPO/scripts/zmin-remote.sh backup"; }
```

**Step 2: Rodar contra o mini**

Run: `zmin backup`
Expected:

```
empacotando o estado do mini (12G total, ~5 MB útil)
backup: /Users/talita/zaya-backups/mini/20261116-213500-zaya.tar.gz (4,8M)
criptografado: não | retenção: 10
```

**Step 3: Verificar conteúdo e permissão**

Run:
```bash
ls -l ~/zaya-backups/mini/ | tail -2
tar tzf ~/zaya-backups/mini/*-zaya.tar.gz | grep -cE '^\./'
shasum -a 256 -c ~/zaya-backups/mini/*-zaya.tar.gz.sha256
```
Expected: `600` no `.tar.gz`, contagem > 30 arquivos, e `OK` no checksum.

**Step 4: Testar cifra**

Run: `printf 'teste\nteste\n' | zmin backup --encrypt --out /tmp/bk`
Expected: `/tmp/bk/*.tar.gz.enc`; `tar tzf` no `.enc` **falha** (é binário) e `openssl enc -d` com a senha devolve o tar.

**Step 5: Commit**

```bash
git add scripts/zmin.sh
git commit -m "feat(zmin): backup com retenção, checksum e cifra opcional"
```

---

### Task 4: `zmin restore`

**Files:**
- Modify: `scripts/zmin.sh`
- Modify: `scripts/zmin-remote.sh` (novo subcomando `restore`)

**Step 1: Subcomando remoto `restore`**

```bash
cmd_restore() {           # lê o tar do stdin; aplica por área
  local areas="${1:-config,data,brain}" dry="${2:-0}"
  local staging; staging=$(mktemp -d "${TMPDIR:-/tmp}/zmin-restore-XXXXXX")
  trap 'rm -rf "$staging"' EXIT
  tar xzf - -C "$staging"
  for area in ${areas//,/ }; do
    case "$area" in
      config) src="$staging/.config/zaya"; dst="$HOME_DIR/.config/zaya" ;;
      data)   src="$staging/.local/share/zaya"; dst="$HOME_DIR/.local/share/zaya" ;;
      brain)  src="$staging/SecondBrain"; dst="$HOME_DIR/SecondBrain" ;;
      *) echo "área desconhecida: $area" >&2; exit 2 ;;
    esac
    [ -d "$src" ] || continue
    echo "restaurando $area → $dst"
    [ "$dry" = "1" ] && { diff -rq "$src" "$dst" 2>/dev/null | head -10; continue; }
    mkdir -p "$dst"
    rsync -a --delete "$src/" "$dst/"
  done
  # permissões de segredo voltam a 600 mesmo se o tar veio de outra máquina
  chmod 600 "$HOME_DIR/.config/zaya/.env" "$HOME_DIR/.config/zaya/agenda.env" 2>/dev/null || true
  chmod 600 "$HOME_DIR/.local/share/zaya/"*.db 2>/dev/null || true
  echo "ok"
}
```

**Step 2: Função no zmin**

Regras de segurança, na ordem: **parar o serviço → snapshot do estado atual → aplicar → permissões → subir serviço → doctor**.

```bash
cmd_restore() {
  local arquivo="${1:-}" areas="config,data,brain" dry=0 sim=0
  shift || true
  while [ $# -gt 0 ]; do
    case "$1" in
      --only) areas="$2"; shift 2 ;;
      --dry-run) dry=1; shift ;;
      --yes) sim=1; shift ;;
      *) die "restore: opção desconhecida: $1" ;;
    esac
  done
  [ -f "$arquivo" ] || die "uso: zmin restore <arquivo.tar.gz|.enc> [--only config,data,brain]"
  [ "$sim" -eq 1 ] || { printf 'isso SOBRESCREVE o estado do mini em [%s]. Continuar? [s/N] ' "$areas"; read -r r; [ "$r" = s ] || exit 1; }
  info "snapshot do estado atual antes de mexer"
  cmd_backup --out "$HOME/zaya-backups/pre-restore" --keep 5
  [ "$dry" -eq 1 ] && info "dry-run: paro aqui"
  …
}
```

**Step 3: Testar o ciclo completo com `ZMIN_HOST=local`**

Requer suporte a host local (Task 5); até lá, teste no mini real **com `--dry-run`**:

Run:
```bash
zmin restore ~/zaya-backups/mini/*-zaya.tar.gz --dry-run
```
Expected: `snapshot do estado atual`, depois `restaurando config → …` com diff vazio (nada mudou desde o backup).

**Step 4: Commit** — `feat(zmin): restore por área, com snapshot automático antes`

---

### Task 5: `ZMIN_HOST=local` (teste de verdade, sem sujar o mini)

**Files:** Modify `scripts/zmin.sh` (definição de `SSH`, `ssh_remote`, `ssh_remote_raw`)

```bash
if [ "$HOST" = "local" ]; then
  SSH=(bash -c)                     # "ssh" vira execução local
  REMOTE_REPO="${ZMIN_LOCAL_REPO:-$HOME/zaya}"
else
  SSH=(ssh -o ConnectTimeout=8 "$HOST")
fi
need_ssh() { "${SSH[@]}" true 2>/dev/null || die "sem SSH em '$HOST'"; }
```

**Teste de aceite do ciclo (o mais importante do plano):**

```bash
tmp=$(mktemp -d)
HOME_FAKE="$tmp/home"; mkdir -p "$HOME_FAKE/.config/zaya" "$HOME_FAKE/.local/share/zaya"
echo 'X=1' >"$HOME_FAKE/.config/zaya/agenda.env"
python3 -c "import sqlite3;c=sqlite3.connect('$HOME_FAKE/.local/share/zaya/zaya.db');c.execute('create table t(a)');c.execute('insert into t values(1)');c.commit()"
HOME="$HOME_FAKE" ZMIN_HOST=local zmin backup --out "$tmp/bk"
rm "$HOME_FAKE/.config/zaya/agenda.env"
HOME="$HOME_FAKE" ZMIN_HOST=local zmin restore "$tmp/bk"/*.tar.gz --yes
test -f "$HOME_FAKE/.config/zaya/agenda.env" && echo "CICLO OK"
```

---

# Fase 2 — `zmin audit`

### Task 6: Coletar fatos dos dois lados

**Files:** Create `scripts/zmin-audit.sh` (invocado por `zmin audit`), Modify `scripts/zmin.sh`

O audit junta três fontes: `zmin-remote.sh info` (mini), leituras locais (Mac) e uma varredura de vazamento da chave nos transcripts.

**Step 1: Escrever os checks como funções com código de saída**

| id | check | PASS quando | como |
|---|---|---|---|
| `mac.env.perms` | `.env` da Agenda no Mac | `600` | `stat -f %Lp` |
| `mac.db.perms` | `zaya.db` no Mac | `600` | idem |
| `mac.filevault` | FileVault | `On` | `fdesetup status` |
| `mac.tm` | Time Machine tem backup | `tmutil latestbackup` não vazio | idem |
| `mini.env.perms` | `.env` e `agenda.env` | `600` | `stat -c %a` |
| `mini.shell_access` | `telegram.shell_access` | `False` | python do venv |
| `mini.auto_approve` | `auto_approve` exige chat fixo | chat preenchido | idem |
| `mini.agenda` | Agenda habilitada e respondendo | `enabled` e 1 query ok | idem |
| `mini.sshd` | `PasswordAuthentication no` | `no` | grep no `sshd_config` |
| `mini.ufw` | firewall ativo | `active` | `systemctl is-active ufw` |
| `mini.crypt` | disco criptografado | `CRYPT > 0` | `lsblk` |
| `mini.disk` | disco < 85% | `DISK_PCT < 85` | `df` |
| `leak.key` | chave-mestra em transcripts/logs | nenhuma ocorrência | python: `like '%<prefixo>%'` em `turns` + grep em `sessions/` |

**Step 2: Saída e código de retorno**

```
PASS  mac.env.perms        ~/agenda-git/agenda/.env 600
WARN  mac.filevault        FileVault desligado
FAIL  mini.sshd            PasswordAuthentication yes
...
7 pass · 3 warn · 1 fail
```

Códigos: `0` tudo pass, `1` só warnings, `2` algum fail. `--json` imprime `{"checks":[…],"summary":{…}}`.

**Step 3: Aceite**

Run: `zmin audit; echo "exit=$?"`
Expected: `exit=2` (hoje: FileVault off, SSH com senha, disco sem crypt).

---

# Fase 3 — `zmin fix`

### Task 7: Auto-conserto do que é seguro

**Files:** Modify `scripts/zmin.sh`

| sintoma | ação | precisa de sudo? |
|---|---|---|
| serviço `inactive` | `systemctl --user restart` + espera + recheca | não |
| IP do alias ≠ IP real | reescreve `~/.ssh/config` com backup e confirmação | não |
| `agenda env` ilegível/ausente | imprime o que falta e como corrigir | não |
| `shell_access=true` | **não muda**: avisa e mostra o trecho a editar | não |
| SSH com senha | imprime `zmin sudo bash …/hardening-ssh.sh` | sim (usuário roda) |
| disco > 85% | sugere `zmin sudo apt clean` e aponta os maiores | não |

Regra: `fix` nunca altera segurança para "consertar conveniência" e nunca roda sudo sozinho.

**Aceite:** derrubar o serviço de propósito (`systemctl --user stop zaya-telegram`), rodar `zmin fix`, e o `doctor` voltar a `active`.

---

# Fase 4 — deploy com rede de proteção

### Task 8: teste antes + snapshot + `--rollback`

**Files:** Modify `scripts/zmin.sh` (`cmd_deploy`), Modify `scripts/zmin-remote.sh` (`snapshot`, `rollback`)

```bash
cmd_deploy() {
  local testar=1 dry=0
  while [ $# -gt 0 ]; do case "$1" in --no-test) testar=0; shift ;;
    --dry-run) dry=1; shift ;; *) break ;; esac; done
  if [ "$testar" -eq 1 ] && [ -x "$LOCAL_REPO/.venv/bin/python" ]; then
    info "rodando a suíte aqui antes de enviar"
    ( cd "$LOCAL_REPO" && .venv/bin/python -m pytest -q ) || die "testes falharam: nada foi enviado"
  fi
  [ "$dry" -eq 1 ] || ssh_remote "bash $REMOTE_REPO/scripts/zmin-remote.sh snapshot"
  …
}

cmd_rollback() { ssh_remote "bash $REMOTE_REPO/scripts/zmin-remote.sh rollback" && cmd_restart; }
```

Remoto: `snapshot` guarda `tar czf ~/.local/share/zmin/deploys/$(date +%s).tar.gz zaya/` e mantém 5; `rollback` desempacota o mais recente e reinicia via `systemctl --user restart zaya-telegram`.

**Aceite:** quebrar um import de propósito (`echo 'raise RuntimeError' >> zaya/__init__.py`), `zmin deploy` **falha no teste local** e não envia; com `--no-test`, o import-check remoto barra; `zmin rollback` devolve o serviço.

---

# Fase 5 — conforto (opcional, depois do Tier 1)

### Task 9: `zmin tarefa` / `zmin feito`

Reusam as ferramentas `agenda_nova_tarefa` / `agenda_marcar_tarefa` (já existem desde a mudança do `shell_access`), com confirmação no terminal do Mac:

```bash
zmin tarefa "comprar leite" --dia 2026-11-17   # mostra e pergunta [s/N]
zmin feito fio dental                          # casa por texto, mostra o id achado
```

Implementação: um `ssh zaya "$(citado do python -m zaya …)"` chamando as ferramentas com `confirm=True` **só depois** do `[s/N]`.

### Task 10: `zmin config` e `zmin logs --erros`

- `zmin config` → `zaya config` remoto (segredos já redigidos).
- `zmin config --edit` → baixa para `$TMPDIR`, abre `$EDITOR`, **valida com `python -c 'Config.load()'`** no mini antes de aplicar, com `.bak`.
- `zmin logs --erros` → `journalctl … | grep -E 'ERROR|WARNING|Traceback'`; `--desde ontem`.

### Task 11: `zmin chat`

REPL simples: mantém **uma** sessão remota (`screen -dmS zaya-chat`) e envia linhas por `screen -S zaya-chat -X stuff`, ou (mais simples) usa `zaya ask` em loop com `--session`. Escolher na implementação; começar com loop de `ask` e medir.

---

# Fase 6 — vigilância (quando o mini virar crítico)

### Task 12: `zmin watchdog`

`~/Library/LaunchAgents/com.zaya.zmin-watchdog.plist`, `StartInterval=900`, chama `zmin watchdog --check`:

```bash
cmd_watchdog() {
  [ "${1:-}" = "--check" ] || die "uso: zmin watchdog --check | --install | --uninstall"
  if "${SSH[@]}" -o ConnectTimeout=5 true 2>/dev/null; then return 0; fi
  # mini fora do ar: avisa pelo Telegram usando o token do .env DESTE Mac
  token=$(grep -m1 '^ZAYA_TELEGRAM_BOT_TOKEN=' "$HOME/.config/zaya/.env" | cut -d= -f2-)
  chat=$(grep -m1 '^ZAYA_TELEGRAM_ALLOWED_CHAT_ID=' "$HOME/.config/zaya/.env" | cut -d= -f2-)
  curl -s -o /dev/null --data-urlencode "chat_id=$chat" \
       --data-urlencode "text=⚠️ o mini não respondem há 15 min (ssh $HOST)" \
       "https://api.telegram.org/bot$token/sendMessage"
}
```

Cuidado: um aviso por hora no máximo (estado em `~/.zmin/watchdog.state`), senão o próprio vigia vira spam.

### Task 13: `zmin rotate-key`

```bash
zmin rotate-key            # pergunta a chave nova, atualiza os DOIS .env com .bak
```
Faz backup antes, troca em `~/agenda-git/agenda/.env` (Mac) e `~/.config/zaya/agenda.env` (mini), roda `zmin test` e `zmin doctor`; se a Agenda não responder, **restaura** os `.bak` e avisa.

---

## Critérios de aceite do plano inteiro (Fases 1–4)

1. `zmin backup` produz arquivo `600` com checksum válido e ≤ 6 MB.
2. `zmin restore` do próprio backup, no modo `ZMIN_HOST=local`, devolve os arquivos removidos (ciclo provado na Task 5).
3. `zmin audit` roda em < 5 s e sai com 2 hoje, 0 depois dos itens pendentes.
4. `zmin fix` conserta serviço caído e alias com IP trocado; nunca abre shell remoto sozinho.
5. `zmin deploy` com teste quebrado **não envia nada**; `zmin rollback` devolve o serviço em < 30 s.
6. `bash scripts/zmin-selftest.sh` verde e rodando no `deploy` como pré-requisito.

## Riscos conhecidos

| risco | mitigação |
|---|---|
| backup contém token do bot e chave-mestra | `600` + `--encrypt` recomendado; documentar que a frase-senha não pode ficar no MacBook |
| FileVault desligado no MacBook | o backup cifrado é justamente para isso; ligar o FileVault é tarefa separada |
| restore com serviço rodando corromperia `zaya.db` | restore para o serviço, snapshot antes, sobe depois |
| `rsync --delete` no restore apaga arquivo novo criado depois do backup | é o comportamento desejado (espelho fiel); por isso `--only` e confirmação |
| bash 3.2 no Mac | sem arrays associativos, sem `${var,,}`; o selftest roda no bash do Mac |
| helper remoto desatualizado | `zmin` detecta ausência do `zmin-remote.sh` e manda rodar `zmin deploy` antes |

## Ordem de execução sugerida

Fase 1 (Tasks 1–5) → Fase 2 (Task 6) → Fase 3 (Task 7) → Fase 4 (Task 8), com um commit por tarefa e `zmin-selftest.sh` verde antes de cada um. Fases 5 e 6 só depois de um dia de uso real das Fases 1–4.
