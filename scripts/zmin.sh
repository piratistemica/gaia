#!/usr/bin/env bash
# zmin — administra a Zaya do Mac mini a partir do MacBook, sem ir até ele.
#
#   zmin doctor            # ssh, serviço, agenda, IP vs alias
#   zmin logs [-f]         # journal do serviço (sem sudo, é unidade de usuário)
#   zmin restart           # reinicia o serviço do Telegram
#   zmin deploy [--dry-run]# manda o código desta pasta e reinicia
#   zmin test              # smoke test das ferramentas/agenda no mini
#   zmin agenda [AAAA-MM-DD]# o que a Zaya vê na Agenda
#   zmin ask "pergunta"    # conversa com a Zaya do mini, sem Telegram
#   zmin serve install     # instala a unidade zaya-serve (NAO liga)
#   zmin serve cutover     # para o Telegram e liga o serve (um processo so)
#   zmin serve logs [-f]   # journal do zaya-serve
#   zmin serve status      # estado do zaya-serve e /health
#   zmin shell             # sessão persistente (screen) no mini
#   zmin sudo <comando…>   # roda com sudo; a senha é digitada AQUI
#
# Tudo por SSH com chave: nada de expor porta, nada de senha por comando.
set -euo pipefail

HOST="${ZMIN_HOST:-zaya}"
LOCAL_REPO="${ZMIN_LOCAL_REPO:-$HOME/zaya}"
REMOTE_REPO="${ZMIN_REMOTE_REPO:-/home/talita/zaya}"
REMOTE_PY="$REMOTE_REPO/.venv/bin/python"
UNIT="zaya-telegram.service"
SERVE_UNIT="zaya-serve.service"

SSH=(ssh -o ConnectTimeout=8 "$HOST")

die() { printf 'zmin: %s\n' "$*" >&2; exit 1; }
info() { printf '\033[1m%s\033[0m\n' "$*"; }

need_ssh() {
  "${SSH[@]}" true 2>/dev/null || die "sem SSH em '$HOST' — rede caiu ou o IP mudou (zmin doctor)"
}

cmd_doctor() {
  need_ssh
  info "== ssh =="
  "${SSH[@]}" 'printf "host=%s  ip=%s  uptime=%s\n" "$(hostnamectl hostname)" \
    "$(hostname -I | awk "{print \$1}")" "$(uptime -p)"'
  local alias_ip
  alias_ip=$(awk '/^Host zaya$/{f=1} f&&/HostName/{print $2; exit}' ~/.ssh/config)
  local real_ip
  real_ip=$("${SSH[@]}" "hostname -I | awk '{print \$1}'")
  if [ "$alias_ip" = "$real_ip" ]; then
    printf 'alias: %s (ok)\n' "$alias_ip"
  else
    printf 'alias: %s — mini está em %s (atualize ~/.ssh/config ou use zaya.local)\n' \
      "$alias_ip" "$real_ip"
  fi

  if ping -c1 -W1 zaya.local >/dev/null 2>&1; then
    printf 'mDNS: zaya.local responde (alternativa ao IP)\n'
  else
    printf 'mDNS: zaya.local nao responde (da para reiniciar o avahi no mini)\n'
  fi

  info "== serviço =="
  "${SSH[@]}" "systemctl --user is-active $UNIT; systemctl --user show -p ActiveEnterTimestamp --value $UNIT"

  info "== agenda + ferramentas =="
  "${SSH[@]}" "cd $REMOTE_REPO && $REMOTE_PY -" <<'PY'
from pathlib import Path
from zaya.config import Config
from zaya.agenda.client import AgendaClient
from zaya.agent.tools import ToolContext, build_tools

cfg = Config.load()
tools = build_tools(ToolContext(Path.cwd(), True, cfg.agenda))
agenda = AgendaClient(cfg.agenda)
print("agenda:", "ok" if agenda.enabled else "DESABILITADA", "| env:", cfg.agenda.env_file)
print("ferramentas de agenda:", [n for n in tools.names() if n.startswith("agenda_")] or "NENHUMA")
print("telegram: auto_approve=%s shell_access=%s" % (
    cfg.telegram.auto_approve, cfg.telegram.shell_access))
PY

  info "== última linha do log =="
  "${SSH[@]}" "tail -n 3 ~/.local/share/zaya/logs/zaya.log 2>/dev/null || echo '(sem log ainda)'"
}

cmd_logs() {
  need_ssh
  if [ "${1:-}" = "-f" ]; then
    "${SSH[@]}" journalctl --user -u "$UNIT" -f -n 40
  else
    "${SSH[@]}" journalctl --user -u "$UNIT" --no-pager -n 40
  fi
}

# Qual unidade é a dona agora: o serve (novo) ou o telegram (antigo).
unit_ativo() {
  "${SSH[@]}" "systemctl --user is-active $SERVE_UNIT >/dev/null 2>&1 \
    && echo $SERVE_UNIT || echo $UNIT"
}

cmd_restart() {
  need_ssh
  local alvo
  alvo="$(unit_ativo)"
  "${SSH[@]}" "systemctl --user restart $alvo && sleep 3 && systemctl --user is-active $alvo"
  printf 'serviço reiniciado em %s (%s)\n' "$HOST" "$alvo"
}

cmd_serve() {
  local acao="${1:-status}"
  need_ssh
  case "$acao" in
    install)
      info "instalando $SERVE_UNIT (sem ligar)"
      "${SSH[@]}" "mkdir -p ~/.config/systemd/user" || die "sem ~/.config/systemd/user"
      scp -q "$LOCAL_REPO/deploy/zaya-serve.service" \
        "$HOST:~/.config/systemd/user/$SERVE_UNIT" || die "falha ao copiar a unidade"
      "${SSH[@]}" "systemctl --user daemon-reload" || die "daemon-reload falhou"
      printf 'unidade instalada (inativa). Para trocar de dono do Telegram:\n'
      printf '  zmin serve cutover\n'
      ;;
    cutover)
      info "parando o Telegram ($UNIT) e ligando o serve ($SERVE_UNIT)"
      "${SSH[@]}" "systemctl --user disable --now $UNIT 2>/dev/null || true; \
        systemctl --user enable --now $SERVE_UNIT"
      sleep 3
      "${SSH[@]}" "systemctl --user is-active $SERVE_UNIT; \
        systemctl --user is-active $UNIT || echo \"$UNIT parado (esperado)\""
      printf 'dono do Telegram agora: %s em %s\n' "$SERVE_UNIT" "$HOST"
      ;;
    logs)
      shift || true
      "${SSH[@]}" "journalctl --user -u $SERVE_UNIT -n 80 --no-pager $*"
      ;;
    status)
      "${SSH[@]}" "systemctl --user is-active $SERVE_UNIT; \
        systemctl --user is-enabled $SERVE_UNIT; \
        curl -sf -m 3 http://127.0.0.1:8787/health || echo 'health: sem resposta (token?)'"
      ;;
    *) die "use: zmin serve install|cutover|logs|status" ;;
  esac
}

cmd_deploy() {
  need_ssh
  local flags=(-a -v --relative)
  [ "${1:-}" = "--dry-run" ] && flags+=(--dry-run)
  info "enviando $LOCAL_REPO → $HOST:$REMOTE_REPO"
  ( cd "$LOCAL_REPO" && rsync "${flags[@]}" \
      --exclude '.git' --exclude '.venv' --exclude 'data/' --exclude 'config/' \
      --exclude '__pycache__' --exclude '.mypy_cache' --exclude '.pytest_cache' \
      --exclude '.ruff_cache' --exclude '.coverage' --exclude 'zaya/zaya/' \
      --exclude '*.dmg' --exclude '*.pdf' --exclude '*.xlsx' --exclude '*.xls' \
      --exclude '*.ods' --exclude '*.numbers' --exclude '*.DS_Store' \
      ./ "$HOST:$REMOTE_REPO/" )
  if [ "${1:-}" = "--dry-run" ]; then
    info "dry-run: nada foi reiniciado"
    return 0
  fi
  "${SSH[@]}" "cd $REMOTE_REPO && $REMOTE_PY -c 'import zaya'" \
    || die "código enviado mas não importa no mini — serviço NÃO reiniciado"
  # cmd_restart escolhe a unidade ativa, então o deploy não reinicia um
  # serviço parado nem deixa o serve rodando código velho.
  cmd_restart
}

cmd_test() {
  need_ssh
  "${SSH[@]}" "cd $REMOTE_REPO && $REMOTE_PY -" <<'PY'
"""Smoke test: o venv do mini não tem pytest, então conferimos o essencial."""
import asyncio
from pathlib import Path
from zaya.config import Config
from zaya.agenda.client import AgendaClient
from zaya.agent.tools import ToolContext, build_tools
from zaya.agent.tools.agenda import build_agenda_tools
from zaya.briefing.compose import collect_tasks
from zaya.telegram.service import telegram_permission

cfg = Config.load()
ctx = ToolContext(Path.cwd(), True, cfg.agenda)
tools = build_tools(ctx)
assert {"read", "bash"} <= set(tools.names()), tools.names()
if cfg.agenda.enabled:
    assert "agenda_dia" in tools.names(), "ferramentas de agenda não apareceram"
    rows = asyncio.run(AgendaClient(cfg.agenda).notes_on(__import__("zaya.agenda.client", fromlist=["x"])._today_local()))
    blanks = [r for r in rows if r.get("is_checkbox") and not str(r.get("text") or "").strip()]
    itens = collect_tasks(rows)
    assert not any(not i.text for i in itens), "tarefa vazia no briefing"
    print("agenda ok: %d notas, %d em branco ignoradas, %d tarefas" % (len(rows), len(blanks), len(itens)))

pol = telegram_permission(cfg.telegram)
print("telegram: bash=%s agenda_dia=%s" % (pol("bash", []), pol("agenda_dia", [])))
print("smoke ok")
PY
}

cmd_agenda() {
  need_ssh
  "${SSH[@]}" "cd $REMOTE_REPO && $REMOTE_PY -" "$1" <<'PY'
import sys
from pathlib import Path
from zaya.config import Config
from zaya.agent.tools import ToolContext
from zaya.agent.tools.agenda import build_agenda_tools

cfg = Config.load()
ctx = ToolContext(Path.cwd(), True, cfg.agenda)
dia = {tool.name: tool for tool in build_agenda_tools()}["agenda_dia"]
args = {"data": sys.argv[1]} if len(sys.argv) > 1 and sys.argv[1] else {}
print(dia.handler(args, ctx).content)
PY
}

cmd_ask() {
  need_ssh
  [ $# -ge 1 ] || die 'uso: zmin ask "sua pergunta"'
  local pergunta="$*"
  "${SSH[@]}" -t "cd $REMOTE_REPO && $REMOTE_PY -m zaya ask --no-voice $(printf '%q' "$pergunta")"
}

cmd_shell() {
  need_ssh
  printf 'entrando em %s (screen: Ctrl+A D sai sem matar a sessão)\n' "$HOST"
  "${SSH[@]}" -t 'screen -Rd zmin || screen -S zmin'
}

cmd_sudo() {
  need_ssh
  [ $# -ge 1 ] || die 'uso: zmin sudo <comando…>'
  # ssh junta os argumentos com espaço e o shell remoto separa de novo: sem
  # citar cada um, comando com espaço (sed, por exemplo) chega cortado.
  local citado=""
  local arg
  for arg in "$@"; do citado+=" $(printf '%q' "$arg")"; done
  printf 'senha do %s (digitada aqui):\n' "$HOST"
  "${SSH[@]}" -t "sudo$citado"
}

main() {
  local sub="${1:-doctor}"
  shift || true
  case "$sub" in
    doctor|status) cmd_doctor ;;
    logs) cmd_logs "$@" ;;
    restart) cmd_restart ;;
    deploy) cmd_deploy "$@" ;;
    test|testes) cmd_test ;;
    agenda) cmd_agenda "${1:-}" ;;
    ask) cmd_ask "$@" ;;
    serve) cmd_serve "$@" ;;
    shell|ssh) cmd_shell ;;
    sudo) cmd_sudo "$@" ;;
    -h|--help|help) sed -n '2,20p' "$0" | grep '^#' | sed 's/^# \{0,1\}//' ;;
    *) die "subcomando desconhecido: $sub (use: zmin help)" ;;
  esac
}

main "$@"
