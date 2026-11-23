#!/usr/bin/env bash
# deploy-gaia — roda NO MINI: deixa o clone igual a origin/main e reinicia.
#
#   deploy-gaia            # atualiza e reinicia se o HEAD mudou
#   deploy-gaia --dry-run  # so mostra o plano
#
# Regras:
#   - o mini e um clone git normal (origin = GitHub). `rsync` nao copia `.git`,
#     e foi assim que o indice dele ficou preso 75 commits atras.
#   - `data/`, `config/`, `.env` e `.venv` estao no .gitignore: `reset --hard`
#     nao toca neles e `git clean -fd` (sem -x) tambem nao.
#   - codigo que nao importa nao reinicia o servico: volta ao SHA anterior.
#   - reinicia a unidade ATIVA, nunca liga as duas (um processo escritor so).
#
# Codigo de saida: 0 = em dia/atualizado, 2 = estado sujo (reset falhou),
#                  3 = falha (rede, repo invalido, import quebrado).
set -euo pipefail

REPO="${GAIA_DEPLOY_REPO:-$HOME/gaia}"
REMOTE="${GAIA_DEPLOY_REMOTE:-origin}"
BRANCH="${GAIA_DEPLOY_BRANCH:-main}"
PY="$REPO/.venv/bin/python"
# Testes trocam por um stub: o script roda no mini, onde systemctl existe.
SYSTEMCTL="${GAIA_DEPLOY_SYSTEMCTL:-systemctl}"
SERVE_UNIT="gaia-serve.service"
TELEGRAM_UNIT="gaia-telegram.service"

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

log() { printf 'deploy-gaia: %s\n' "$*"; }
die() { log "$*"; exit 3; }

[ -d "$REPO/.git" ] || die "$REPO nao e um repositorio git"
cd "$REPO"
git rev-parse --git-dir >/dev/null 2>&1 || die "$REPO nao e um repositorio git valido"

git fetch --prune "$REMOTE" "$BRANCH" >/dev/null 2>&1 \
  || die "git fetch falhou (rede? remote?) — nada foi alterado"

atual="$(git rev-parse HEAD)"
alvo="$(git rev-parse "$REMOTE/$BRANCH")"

if [ "$atual" = "$alvo" ]; then
  log "ja em $alvo — nada a fazer"
  exit 0
fi

log "$atual -> $alvo ($(git log -1 --format=%s "$alvo"))"
if [ "$DRY" = 1 ]; then
  log "--dry-run: nada foi alterado nem reiniciado"
  exit 0
fi

git reset --hard "$alvo" >/dev/null || die "reset --hard falhou (estado sujo?) — nada reiniciado"
# Sem -x: o que esta no .gitignore (data/, config/, .env, .venv) fica.
git clean -fdq

if ! "$PY" -c 'import gaia' >/dev/null 2>&1; then
  git reset --hard "$atual" >/dev/null || exit 2
  die "codigo novo nao importa — voltei para $atual e NAO reiniciei o servico"
fi

if "$SYSTEMCTL" --user is-active --quiet "$SERVE_UNIT" 2>/dev/null; then
  unidade="$SERVE_UNIT"
else
  unidade="$TELEGRAM_UNIT"
fi
"$SYSTEMCTL" --user restart "$unidade" || die "falha ao reiniciar $unidade"
log "reiniciado $unidade em $alvo"
