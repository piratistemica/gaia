#!/usr/bin/env bash
# sb-sync — mantem o SecondBrain igual no MacBook e no mini, via git.
#
#   sb-sync            # sincroniza (padrao)
#   sb-sync status     # so mostra o estado, nao mexe em nada
#   sb-sync --help
#
# Regras que este script segue (e que voce nao deve furar na mao):
#   - o SecondBrain e um repositorio git; o banco (`gaia.db`) e o indice FTS
#     NUNCA entram nele (estao no .gitignore). Sincronizar banco corrompe.
#   - cada maquina commita o que escreveu (memoria, sonhos, notas) e empurra.
#   - conflito NUNCA e resolvido automaticamente: o script aborta, deixa a
#     arvore limpa e manda resolver a mao. Perder texto seu e pior que ficar
#     um minuto sem sincronizar.
#
# Codigo de saida: 0 = sincronizado, 2 = conflito (resolver a mao),
#                  3 = outra falha (rede, remote, repo sujo).
set -euo pipefail

SB="${GAIA_SECONDBRAIN:-$HOME/SecondBrain}"
REMOTE="${GAIA_SB_REMOTE:-origin}"
BRANCH="${GAIA_SB_BRANCH:-main}"
NAME="$(hostname -s | tr 'A-Z' 'a-z')"

die() { printf 'sb-sync: %s\n' "$*" >&2; exit 3; }
info() { printf '\033[1m%s\033[0m\n' "$*"; }

[ -d "$SB/.git" ] || die "$SB nao e um repositorio git"

cd "$SB"

# Identidade: o mini nao tem git config global; sem isso o commit falha.
git config user.name  >/dev/null || die "sem user.name no git"
git config user.email >/dev/null || die "sem user.email no git"

estado() {
  local ahead behind
  ahead="$(git rev-list --count "$REMOTE/$BRANCH..HEAD" 2>/dev/null || echo "?")"
  behind="$(git rev-list --count "HEAD..$REMOTE/$BRANCH" 2>/dev/null || echo "?")"
  printf 'repo:   %s\nramo:   %s (%s)\nsujo:   %s arquivo(s)\na frente: %s | atras: %s\n' \
    "$SB" "$BRANCH" "$REMOTE" "$(git status --porcelain | wc -l | tr -d ' ')" "$ahead" "$behind"
}

if [ "${1:-sync}" = "status" ]; then
  git fetch --quiet "$REMOTE" "$BRANCH" || die "sem rede/remote"
  estado
  exit 0
fi

case "${1:-sync}" in
  sync) ;;
  -h|--help|help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "uso: sb-sync [status|--help]" ;;
esac

# 1) O que esta escrito aqui vira commit.
if [ -n "$(git status --porcelain)" ]; then
  n="$(git status --porcelain | wc -l | tr -d ' ')"
  git add -A
  git commit -q -m "sync($NAME): $n arquivo(s) — $(date '+%Y-%m-%d %H:%M')" \
    || die "commit falhou"
  info "commit local: $n arquivo(s)"
fi

# 2) Traz o que a outra maquina escreveu, com autostash (o trabalho daqui ja
#    esta commitado, mas o autostash cobre arquivo nao rastreado).
git fetch --quiet "$REMOTE" "$BRANCH" || die "sem rede/remote"

if ! git pull --quiet --rebase --autostash "$REMOTE" "$BRANCH"; then
  git rebase --abort 2>/dev/null || true
  git stash pop --quiet 2>/dev/null || true
  printf 'sb-sync: CONFLITO ao trazer %s/%s\n' "$REMOTE" "$BRANCH" >&2
  git status --short >&2
  printf 'Resolva a mao em %s e rode de novo. Nada foi perdido.\n' "$SB" >&2
  exit 2
fi

# 3) Empurra. Se alguem empurrou no meio do caminho, tenta uma vez mais.
if ! git push --quiet "$REMOTE" "$BRANCH" 2>/dev/null; then
  git fetch --quiet "$REMOTE" "$BRANCH" || die "sem rede/remote"
  if ! git pull --quiet --rebase --autostash "$REMOTE" "$BRANCH"; then
    git rebase --abort 2>/dev/null || true
    printf 'sb-sync: CONFLITO no push — resolva a mao\n' >&2
    exit 2
  fi
  git push --quiet "$REMOTE" "$BRANCH" || die "push rejeitado"
fi

info "sincronizado: $NAME -> $REMOTE/$BRANCH"
