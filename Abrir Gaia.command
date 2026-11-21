#!/bin/zsh
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
export GAIA_CONFIG="$REPO_DIR/config/config.toml"
export GAIA_DOTENV="$REPO_DIR/config/.env"
export GAIA_DATA_DIR="$REPO_DIR/data"
cd "$REPO_DIR" || exit 1
exec "$REPO_DIR/.venv/bin/gaia" "$@"
