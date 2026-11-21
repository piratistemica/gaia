#!/usr/bin/env bash
# Endurece o servidor SSH do mini: desliga login por senha e login de root.
# Rode uma vez, do MacBook:
#   gmin sudo bash /home/talita/gaia/scripts/hardening-ssh.sh
#
# Seguro por construção: faz backup, valida com `sshd -t` antes de recarregar e
# restaura o backup se a configuração ficar inválida. Autenticação por chave
# (a que o MacBook usa) não é afetada por nenhuma das duas mudanças.
set -euo pipefail

CONF=/etc/ssh/sshd_config

if [ "$(id -u)" -ne 0 ]; then
  echo "precisa de root: use 'gmin sudo bash $0'" >&2
  exit 1
fi

BACKUP="$CONF.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$CONF" "$BACKUP"
echo "backup: $BACKUP"

# -E evita o escape de '?'; o padrão casa a linha esteja comentada ou não.
sed -i -E 's/^#?[[:space:]]*PasswordAuthentication[[:space:]].*/PasswordAuthentication no/' "$CONF"
sed -i -E 's/^#?[[:space:]]*PermitRootLogin[[:space:]].*/PermitRootLogin no/' "$CONF"

# Se a diretiva não existia (arquivo sem a linha), acrescenta.
grep -qE '^PasswordAuthentication[[:space:]]+no' "$CONF" ||
  echo 'PasswordAuthentication no' >>"$CONF"
grep -qE '^PermitRootLogin[[:space:]]+no' "$CONF" ||
  echo 'PermitRootLogin no' >>"$CONF"

if ! sshd -t; then
  echo "configuração inválida: restaurando $BACKUP" >&2
  cp -p "$BACKUP" "$CONF"
  exit 1
fi

echo "--- valores efetivos:"
sshd -T | grep -Ei '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication)'

systemctl reload ssh 2>/dev/null || systemctl restart ssh
echo "--- serviço: $(systemctl is-active ssh)"
echo "ok: senha e root desligados; chave continua valendo"
