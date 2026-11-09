#!/usr/bin/env bash
# Create a private .env with freshly generated secrets. Nothing is printed, and an existing .env is
# never overwritten. Afterwards open .env in an editor to add your own API keys.
#   scripts/init-env.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env already exists; leaving it untouched."
  exit 0
fi

umask 077
cp .env.example .env
chmod 600 .env

set_env() {
  if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
set_env SECRET_KEY "$(openssl rand -hex 32)"
set_env ENCRYPTION_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
set_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"

echo "Created .env with fresh secrets (readable only by you; git never commits it)."
echo "Open it in an editor to add your API keys, e.g.:  nano .env"
