#!/usr/bin/env bash
# One-command production setup for a fresh Ubuntu server (Oracle Cloud Always Free, Hetzner, DigitalOcean, ...).
# Installs Docker, opens the firewall, adds swap, writes a .env with fresh secrets, and starts the full
# stack with automatic HTTPS. Safe to re-run: later runs update the code and restart, keeping your .env.
#
#   curl -fsSL https://raw.githubusercontent.com/saksham-eng560/autoapply-ai/main/scripts/server-setup.sh | bash
#
# Optional environment variables (put them before `bash`, e.g. `... | DOMAIN=jobs.example.com bash`):
#   DOMAIN             your domain (its DNS A record must point to this server).
#                      Default: <public-ip>.sslip.io, a free hostname that works with HTTPS out of the box.
#   BRANCH             git branch to deploy (default: main).
#   REPO_URL           git repository (default: the public AutoApply AI repo).
#   APP_DIR            install directory (default: ~/autoapply-ai).
#   WITH_OLLAMA=1      also run a free AI model on this server with Ollama (in Docker, never exposed to the
#                      internet) and use it for scoring, tailoring and answers. Safe to add on a re-run.
#   OLLAMA_MODEL       the Ollama model to download (default: qwen3.5:4b, about 3.4 GB of disk).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/saksham-eng560/autoapply-ai.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-$HOME/autoapply-ai}"
WITH_OLLAMA="${WITH_OLLAMA:-0}"
DEFAULT_OLLAMA_MODEL="qwen3.5:4b"
COMPOSE=(docker compose -f docker-compose.prod.yml)

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }
sudo_() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

install_packages() {
  log "Installing Docker and tools"
  sudo_ apt-get update -qq
  sudo_ env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl openssl ca-certificates iptables-persistent >/dev/null
  if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo_ sh
  fi
  sudo_ systemctl enable --now docker >/dev/null
  if [ "$(id -u)" -ne 0 ] && ! id -nG | grep -qw docker; then
    sudo_ usermod -aG docker "$USER"
  fi
}

open_firewall() {
  # Oracle's Ubuntu images ship iptables rules that reject everything except SSH.
  log "Opening ports 80 and 443 in the server firewall"
  for port in 443 80; do
    sudo_ iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || sudo_ iptables -I INPUT -p tcp --dport "$port" -j ACCEPT
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then sudo_ netfilter-persistent save >/dev/null 2>&1 || true; fi
  if command -v ufw >/dev/null 2>&1 && sudo_ ufw status | grep -q "Status: active"; then
    sudo_ ufw allow 80/tcp >/dev/null && sudo_ ufw allow 443/tcp >/dev/null
  fi
}

add_swap() {
  local mem_mb
  mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  if [ "$mem_mb" -lt 8000 ] && ! swapon --show | grep -q .; then
    log "Adding 4 GB swap (server has ${mem_mb} MB RAM)"
    sudo_ fallocate -l 4G /swapfile && sudo_ chmod 600 /swapfile
    sudo_ mkswap /swapfile >/dev/null && sudo_ swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo_ tee -a /etc/fstab >/dev/null
  fi
}

fetch_code() {
  if [ -d "$APP_DIR/.git" ]; then
    log "Updating code in $APP_DIR ($BRANCH)"
    git -C "$APP_DIR" fetch -q origin "$BRANCH"
    git -C "$APP_DIR" checkout -q "$BRANCH"
    git -C "$APP_DIR" pull -q --ff-only origin "$BRANCH"
  else
    log "Downloading AutoApply AI ($BRANCH) into $APP_DIR"
    git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi
  [ -f "$APP_DIR/docker-compose.prod.yml" ] || die "branch '$BRANCH' does not contain the app yet (try BRANCH=<your branch>)"
}

set_env() {  # set_env KEY VALUE  -> replaces or appends KEY=VALUE in .env
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

env_get() {  # env_get KEY -> its value in .env without inline comment / quotes (empty if missing)
  sed -n "s/^$1=//p" .env | tail -n 1 | sed -e 's/^#.*$//' -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    -e 's/^"\(.*\)"$/\1/'
}

set_env_if_empty() {  # set_env_if_empty KEY VALUE -> only when KEY is missing or empty in .env
  [ -z "$(env_get "$1")" ] || return 1
  set_env "$1" "$2"
}

compose_() {
  if docker info >/dev/null 2>&1; then "${COMPOSE[@]}" "$@"; else sudo_ "${COMPOSE[@]}" "$@"; fi
}

public_ip() {
  curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 10 https://ifconfig.me 2>/dev/null || true
}

write_env() {
  cd "$APP_DIR"
  if [ -f .env ]; then
    log "Keeping existing .env"
    [ -n "${ANTHROPIC_API_KEY:-}" ] && set_env ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
    [ -n "${DOMAIN:-}" ] && set_env DOMAIN "$DOMAIN"
    return
  fi
  log "Creating .env with fresh secrets"
  local domain="${DOMAIN:-}"
  if [ -z "$domain" ]; then
    local ip
    ip=$(public_ip)
    [ -n "$ip" ] || die "could not detect the public IP; re-run with DOMAIN=your.domain"
    domain="${ip//./-}.sslip.io"
  fi
  cp .env.example .env
  chmod 600 .env
  set_env ENVIRONMENT production
  set_env DOMAIN "$domain"
  set_env FRONTEND_URL "https://$domain"
  set_env PUBLIC_API_URL "https://$domain"
  set_env CORS_ORIGINS "https://$domain"
  set_env COOKIE_SECURE true
  set_env SECRET_KEY "$(openssl rand -hex 32)"
  set_env ENCRYPTION_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
  set_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  set_env ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}"
}

configure_ollama() {  # WITH_OLLAMA=1: Ollama container (compose profile) + .env entries; existing values are kept
  [ "$WITH_OLLAMA" = 1 ] || return 0
  cd "$APP_DIR"
  log "Setting up free AI with Ollama (in Docker on this server, never exposed to the internet)"
  local profiles url
  profiles="$(env_get COMPOSE_PROFILES)"
  case ",$profiles," in
    *,ollama,*) ;;
    ",,") set_env COMPOSE_PROFILES ollama ;;
    *) set_env COMPOSE_PROFILES "$profiles,ollama" ;;
  esac
  if [ -n "${OLLAMA_MODEL:-}" ]; then
    set_env OLLAMA_MODEL "$OLLAMA_MODEL"  # chosen explicitly for this run
  else
    set_env_if_empty OLLAMA_MODEL "$DEFAULT_OLLAMA_MODEL" || true
  fi
  set_env_if_empty LLM_PROVIDER ollama || true
  url="$(env_get OLLAMA_BASE_URL)"
  case "$url" in
    ""|http://localhost:11434|http://127.0.0.1:11434) set_env OLLAMA_BASE_URL http://ollama:11434 ;;
    http://ollama:11434) ;;
    *) echo "Keeping the OLLAMA_BASE_URL already in .env" ;;
  esac
  echo "Model: $(env_get OLLAMA_MODEL)   LLM_PROVIDER: $(env_get LLM_PROVIDER)"
  if [ "$(env_get LLM_PROVIDER)" != "ollama" ]; then
    echo "Note: LLM_PROVIDER isn't 'ollama', so an Anthropic / OpenAI key in .env (if set) is used first and Ollama is the fallback."
  fi
}

model_size() {
  case "$1" in
    qwen3.5:4b) echo "about 3.4 GB" ;;
    qwen3.5:9b) echo "about 6.6 GB" ;;
    qwen3:4b) echo "about 2.5 GB" ;;
    qwen3:8b) echo "about 5.2 GB" ;;
    llama3.2:3b|llama3.2) echo "about 2 GB" ;;
    *) echo "a few GB" ;;
  esac
}

start_stack() {
  cd "$APP_DIR"
  log "Building and starting the stack (first build takes 5-15 minutes)"
  compose_ up -d --build --remove-orphans  # sudo when the docker group isn't active in this login session yet
}

pull_ollama_model() {
  [ "$WITH_OLLAMA" = 1 ] || return 0
  cd "$APP_DIR"
  local model
  model="$(env_get OLLAMA_MODEL)"; model="${model:-$DEFAULT_OLLAMA_MODEL}"
  case "$model" in *-cloud|*:cloud) log "$model is a cloud model: nothing to download"; return 0 ;; esac
  log "Downloading the AI model $model inside the ollama container ($(model_size "$model") of disk, one time)"
  for _ in $(seq 1 30); do
    compose_ exec -T ollama ollama list >/dev/null 2>&1 && break
    sleep 2
  done
  if compose_ exec -T ollama ollama pull "$model"; then
    printf '\nModel %s is ready. Disk used by Ollama models: %s\n' "$model" \
      "$(compose_ exec -T ollama du -sh /root/.ollama 2>/dev/null | cut -f1 || echo unknown)"
    echo "Without a GPU each AI answer takes about 1-3 minutes on this server; scans give it the top"
    echo "OLLAMA_MAX_EVALUATIONS_PER_SCAN (15) jobs and score the rest instantly."
  else
    echo "Could not download $model now. Press \"Download model\" in Settings > Integrations later, or run:"
    echo "  cd $APP_DIR && docker compose -f docker-compose.prod.yml exec ollama ollama pull $model"
  fi
}

wait_until_live() {
  local domain
  domain=$(grep '^DOMAIN=' "$APP_DIR/.env" | cut -d= -f2 | awk '{print $1}')
  log "Waiting for https://$domain to come up (HTTPS certificate + database migrations)"
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 10 "https://$domain/health" >/dev/null 2>&1; then
      printf '\n\033[1;32mAutoApply AI is live: https://%s\033[0m\n' "$domain"
      cat <<EOF

Next steps:
  1. Open https://$domain and create your account.
  2. Then block other sign-ups:  cd $APP_DIR && sed -i "s/^ALLOW_REGISTRATION=.*/ALLOW_REGISTRATION=false/" .env && docker compose -f docker-compose.prod.yml up -d
  3. Add your API keys privately with an editor (never on the command line):
       nano $APP_DIR/.env    then:  cd $APP_DIR && docker compose -f docker-compose.prod.yml up -d
  4. Settings > Integrations > AI model shows which AI is active; press "Test AI" to try it.
     No API key? Re-run this script with WITH_OLLAMA=1 in front of bash for a free model on this server.
Logs:    cd $APP_DIR && docker compose -f docker-compose.prod.yml logs -f
Update:  re-run this script
EOF
      return
    fi
    sleep 10
  done
  cat <<EOF

The containers are running but https://$domain is not answering yet. Check:
  - Oracle Cloud: the subnet's security list must allow TCP 80 and 443 from 0.0.0.0/0 (see README).
  - DNS: if you used your own DOMAIN, its A record must point to this server's public IP.
  - Logs: cd $APP_DIR && docker compose -f docker-compose.prod.yml logs caddy api
EOF
}

# shellcheck disable=SC1091
[ -f /etc/os-release ] && . /etc/os-release
[ "${ID:-}" = "ubuntu" ] || [ "${ID_LIKE:-}" = "debian" ] || [ "${ID:-}" = "debian" ] || die "this script supports Ubuntu/Debian"

install_packages
open_firewall
add_swap
fetch_code
write_env
configure_ollama
start_stack
pull_ollama_model
wait_until_live
