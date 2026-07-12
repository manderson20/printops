#!/usr/bin/env bash
# One-command first-run setup for a fresh PrintOps install: installs missing
# prerequisites (via bootstrap.sh), generates real secrets, wires up your
# domain, brings up Postgres/Redis + the API + the web app, and optionally
# installs everything as systemd services with automatic HTTPS (via Caddy +
# Let's Encrypt).
#
# This is the only script you need to run — on a completely fresh machine,
# it installs Node/pnpm, Python/uv, and Docker itself before continuing.
#
# Safe to re-run: it asks before overwriting any .env file that already
# exists, since regenerating secrets invalidates existing sessions and can
# make already-encrypted data (Google SSO/Workspace credentials, etc.)
# unreadable.
#
# Usage: ./scripts/setup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

ask() {
  local prompt="$1" default="${2:-}" reply
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " reply
    echo "${reply:-$default}"
  else
    read -r -p "$prompt: " reply
    echo "$reply"
  fi
}

confirm() {
  local prompt="$1" reply
  read -r -p "$prompt [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

gen_hex() { openssl rand -hex "$1"; }
gen_fernet_key() { python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"; }
gen_password() { python3 -c "import secrets; print(secrets.token_urlsafe(16))"; }

refresh_path() {
  # Picks up tools bootstrap.sh may have just installed in a *subprocess* —
  # nvm/uv add themselves to ~/.bashrc for future shells, which does nothing
  # for the shell already running this script.
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck disable=SC1091
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
  esac
}

ensure_prereqs() {
  local missing=()
  have docker || missing+=("docker")
  have uv || missing+=("uv")
  have pnpm || missing+=("pnpm")
  have openssl || missing+=("openssl")
  have python3 || missing+=("python3")

  if [ "${#missing[@]}" -gt 0 ]; then
    log "Installing missing prerequisites (${missing[*]})"
    "$REPO_ROOT/scripts/bootstrap.sh"
    refresh_path
  fi

  missing=()
  have docker || missing+=("docker")
  have uv || missing+=("uv")
  have pnpm || missing+=("pnpm")
  have openssl || missing+=("openssl")
  have python3 || missing+=("python3")
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "Still missing after running bootstrap.sh: ${missing[*]}"
    echo "Open a new shell (so PATH picks up nvm/uv) and re-run ./scripts/setup.sh."
    exit 1
  fi
}

env_exists_guard() {
  local path="$1"
  if [ -f "$path" ]; then
    warn "$path already exists."
    if ! confirm "Overwrite it with newly generated values? (breaks existing sessions/encrypted data)"; then
      echo "Keeping existing $path unchanged."
      return 1
    fi
  fi
  return 0
}

main() {
  ensure_prereqs

  log "Domain"
  echo "Leave this blank for a local/dev setup (http://localhost). Set it if you're"
  echo "installing this on a server other people will reach over the network —"
  echo "e.g. print.yourschool.org. Point DNS at this box's IP before continuing"
  echo "if you plan to enable automatic HTTPS below."
  DOMAIN="$(ask "Public domain (blank for local/dev)" "")"

  ENABLE_TLS=false
  LETSENCRYPT_EMAIL=""
  if [ -n "$DOMAIN" ]; then
    if confirm "Automatically configure HTTPS for $DOMAIN via Caddy + Let's Encrypt?"; then
      ENABLE_TLS=true
      LETSENCRYPT_EMAIL="$(ask "Contact email for Let's Encrypt (renewal/expiry notices)" "")"
      while [ -z "$LETSENCRYPT_EMAIL" ]; do
        echo "An email is required by Let's Encrypt."
        LETSENCRYPT_EMAIL="$(ask "Contact email for Let's Encrypt" "")"
      done
    fi
  fi

  log "Admin account"
  echo "This sets the initial login (Settings -> Integrations -> Google Sign-In"
  echo "can replace it with real per-person accounts later; see the README)."
  ADMIN_USERNAME="$(ask "Admin username" "admin")"
  ADMIN_PASSWORD="$(ask "Admin password (blank to auto-generate a strong one)" "")"
  if [ -z "$ADMIN_PASSWORD" ]; then
    ADMIN_PASSWORD="$(gen_password)"
    GENERATED_ADMIN_PASSWORD=true
  else
    GENERATED_ADMIN_PASSWORD=false
  fi

  INSTALL_SERVICES=false
  if confirm "Install the API and web app as systemd services (start on boot, restart on failure)?"; then
    INSTALL_SERVICES=true
  fi

  # ---- infra/.env ----
  if env_exists_guard "infra/.env"; then
    log "Writing infra/.env"
    POSTGRES_PASSWORD="$(gen_hex 24)"
    cat >infra/.env <<EOF
POSTGRES_USER=printops
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=printops
POSTGRES_PORT=5432

REDIS_PORT=6379
EOF
  else
    POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' infra/.env | cut -d= -f2-)"
  fi

  # ---- apps/api/.env ----
  if env_exists_guard "apps/api/.env"; then
    log "Writing apps/api/.env"
    if [ -n "$DOMAIN" ]; then
      CORS_ORIGINS="[\"https://${DOMAIN}\"]"
      ENVIRONMENT="production"
    else
      CORS_ORIGINS="[\"http://localhost:3000\"]"
      ENVIRONMENT="development"
    fi
    cat >apps/api/.env <<EOF
PRINTOPS_ENVIRONMENT=${ENVIRONMENT}
PRINTOPS_CORS_ORIGINS=${CORS_ORIGINS}

PRINTOPS_JWT_SECRET=$(gen_hex 32)
PRINTOPS_JWT_ALGORITHM=HS256
PRINTOPS_JWT_EXPIRES_MINUTES=60

PRINTOPS_DEV_USERNAME=${ADMIN_USERNAME}
PRINTOPS_DEV_PASSWORD=${ADMIN_PASSWORD}

PRINTOPS_DATABASE_URL=postgresql+asyncpg://printops:${POSTGRES_PASSWORD}@localhost:5432/printops

PRINTOPS_BACKEND_TOKEN=$(gen_hex 32)
PRINTOPS_ENCRYPTION_KEY=$(gen_fernet_key)

PRINTOPS_REDIS_URL=redis://localhost:6379/0
EOF
    chmod 600 apps/api/.env
  fi

  # ---- apps/web/.env.local ----
  if env_exists_guard "apps/web/.env.local"; then
    log "Writing apps/web/.env.local"
    if [ -n "$DOMAIN" ]; then
      API_URL="https://${DOMAIN}"
    else
      API_URL="http://localhost:8000"
    fi
    cat >apps/web/.env.local <<EOF
NEXT_PUBLIC_API_URL=${API_URL}
EOF
  fi

  # ---- infra: Postgres + Redis ----
  log "Starting Postgres + Redis (docker compose)"
  # On a machine where bootstrap.sh just added this user to the `docker`
  # group moments ago, that membership isn't active in this process yet
  # (it takes a new login/`newgrp`) — fall back to sudo for this run so
  # "just run one script" holds even on a completely fresh machine.
  DC="docker compose"
  if ! docker info >/dev/null 2>&1; then
    warn "Docker group membership isn't active in this shell yet — using sudo for this run."
    warn "Log out/in afterward so future 'docker compose' commands don't need sudo."
    DC="sudo docker compose"
  fi
  (cd infra && $DC up -d)
  echo "Waiting for Postgres to be healthy..."
  for _ in $(seq 1 30); do
    status="$(cd infra && $DC ps --format json postgres 2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read() or '{}').get('Health',''))" 2>/dev/null || true)"
    [ "$status" = "healthy" ] && break
    sleep 1
  done

  # ---- API: deps + migrations ----
  log "Installing API dependencies (uv sync) and running migrations"
  (cd apps/api && uv sync --frozen --extra dev && .venv/bin/alembic upgrade head)

  # ---- Web: deps + build ----
  log "Installing web dependencies and building"
  pnpm install
  (cd apps/web && pnpm build)

  # ---- CUPS held-job spool permissions ----
  # The CUPS backend (infra/cups/backends/printops) runs as root under
  # CUPS's own `lp` group and spools held documents to
  # /var/spool/printops-held as root:lp, group-writable (not world-
  # writable). The API process needs to be in `lp` too, or it can't read/
  # delete those files at release time. Safe to re-run: usermod -aG is
  # additive, and the mkdir/chown/chmod below just re-asserts the same
  # state if it's already correct.
  log "Adding $(id -un) to the lp group (for reading held print jobs)"
  RUN_USER="$(id -un)"
  RUN_GROUP="$(id -gn)"
  sudo usermod -aG lp "$RUN_USER"
  sudo mkdir -p /var/spool/printops-held
  sudo chown root:lp /var/spool/printops-held
  sudo chmod 2770 /var/spool/printops-held
  if systemctl is-active --quiet printops-api 2>/dev/null; then
    warn "printops-api was already running before this group change — its process"
    warn "won't pick up the new lp membership until it restarts."
    warn "Restarting it now so held-job release keeps working."
    sudo systemctl restart printops-api
  fi

  # ---- systemd services ----
  if [ "$INSTALL_SERVICES" = true ]; then
    log "Installing systemd services"
    NODE_BIN_DIR="$(dirname "$(command -v pnpm)")"

    render_unit() {
      local template="$1" out="$2"
      sed \
        -e "s#__PRINTOPS_USER__#${RUN_USER}#g" \
        -e "s#__PRINTOPS_GROUP__#${RUN_GROUP}#g" \
        -e "s#__PRINTOPS_HOME__#${REPO_ROOT}#g" \
        -e "s#__PRINTOPS_NODE_BIN__#${NODE_BIN_DIR}#g" \
        "$template" | sudo tee "$out" >/dev/null
    }

    render_unit infra/systemd/printops-api.service.template /etc/systemd/system/printops-api.service
    render_unit infra/systemd/printops-web.service.template /etc/systemd/system/printops-web.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now printops-api printops-web
  fi

  # ---- Caddy + Let's Encrypt ----
  if [ "$ENABLE_TLS" = true ]; then
    log "Configuring HTTPS for ${DOMAIN} via Caddy"
    if ! have caddy; then
      if ! confirm "Caddy is not installed. Install it now (adds an apt repo, needs sudo)?"; then
        warn "Skipping HTTPS setup — install Caddy manually and re-run, or render infra/Caddyfile.template yourself."
        ENABLE_TLS=false
      else
        sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
        sudo apt-get update -y
        sudo apt-get install -y caddy
      fi
    fi
    if [ "$ENABLE_TLS" = true ]; then
      if [ -f /etc/caddy/Caddyfile ]; then
        sudo cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%s)"
        warn "Backed up existing /etc/caddy/Caddyfile before overwriting."
      fi
      sed \
        -e "s#__PRINTOPS_DOMAIN__#${DOMAIN}#g" \
        -e "s#__LETSENCRYPT_EMAIL__#${LETSENCRYPT_EMAIL}#g" \
        infra/Caddyfile.template | sudo tee /etc/caddy/Caddyfile >/dev/null
      sudo systemctl enable --now caddy
      sudo systemctl reload caddy
      echo "Caddy will request a Let's Encrypt certificate for ${DOMAIN} on first"
      echo "request — this only succeeds once DNS for that domain points here."
    fi
  fi

  # ---- Summary ----
  log "Done"
  if [ "$INSTALL_SERVICES" = true ]; then
    if [ "$ENABLE_TLS" = true ]; then
      echo "PrintOps should be reachable at: https://${DOMAIN}"
    else
      echo "PrintOps is running as systemd services. Reach it at:"
      echo "  http://localhost:3000 (or this box's LAN IP, port 3000)"
    fi
  else
    echo "Services are not installed to run persistently. Start them yourself:"
    echo "  cd apps/api && .venv/bin/uvicorn app.main:app --reload --port 8000"
    echo "  cd apps/web && pnpm dev"
  fi
  echo
  echo "Log in with:"
  echo "  username: ${ADMIN_USERNAME}"
  echo "  password: ${ADMIN_PASSWORD}"
  if [ "$GENERATED_ADMIN_PASSWORD" = true ]; then
    echo "  (auto-generated — copy it now, it is only ever printed here. It's also"
    echo "   in apps/api/.env as PRINTOPS_DEV_PASSWORD if you need it again.)"
  fi
  echo
  echo "Next steps once you're logged in:"
  echo "  - Settings -> Integrations -> Google Sign-In: set up real per-person"
  echo "    accounts with admin/viewer roles (see docs/google-sso-setup.md)."
  echo "  - Settings -> Integrations -> Google Workspace: device attribution sync."
  echo "  - apps/api/.env now holds real secrets. It's gitignored — never commit it,"
  echo "    and keep a secure backup of it somewhere outside this repo."
}

main "$@"
