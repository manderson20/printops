#!/usr/bin/env bash
# Bootstraps a fresh Ubuntu/Debian dev box with everything PrintOps needs to
# build and run: Node/pnpm, Python tooling (uv), Docker, and the GitHub CLI.
#
# Idempotent: safe to re-run. Each tool is installed only if missing.
# Add new tools as their own install_<tool> function + a call at the bottom.
#
# Usage: ./scripts/bootstrap.sh [--yes]
#   --yes   skip the confirmation prompt before sudo-requiring steps (Docker)

set -euo pipefail

NODE_VERSION="20"
PNPM_VERSION="9"
ASSUME_YES=false

for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=true ;;
  esac
done

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

confirm_sudo_step() {
  local what="$1"
  if [ "$ASSUME_YES" = true ]; then
    return 0
  fi
  read -r -p "About to run sudo commands to install ${what}. Continue? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

install_node_pnpm() {
  log "Node.js ${NODE_VERSION} + pnpm ${PNPM_VERSION}"
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
  fi
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  if ! nvm ls "$NODE_VERSION" >/dev/null 2>&1; then
    nvm install "$NODE_VERSION"
  fi
  nvm alias default "$NODE_VERSION" >/dev/null
  corepack enable
  corepack prepare "pnpm@${PNPM_VERSION}" --activate
}

install_uv() {
  log "uv (Python toolchain manager)"
  if have uv; then
    echo "uv already installed: $(uv --version)"
    return
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
}

install_gh_cli() {
  log "GitHub CLI (gh)"
  if have gh; then
    echo "gh already installed: $(gh --version | head -1)"
    return
  fi
  if ! confirm_sudo_step "the GitHub CLI apt repository"; then
    echo "Skipped gh install."
    return
  fi
  sudo mkdir -p -m 755 /etc/apt/keyrings
  out=$(mktemp)
  wget -nv -O "$out" https://cli.github.com/packages/githubcli-archive-keyring.gpg
  sudo cp "$out" /etc/apt/keyrings/githubcli-archive-keyring.gpg
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y gh
}

install_docker() {
  log "Docker Engine + Compose plugin"
  if have docker; then
    echo "Docker already installed: $(docker --version)"
  else
    if ! confirm_sudo_step "Docker Engine (adds an apt repo, installs docker-ce)"; then
      echo "Skipped Docker install."
      return
    fi
    sudo apt-get update -y
    sudo apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi

  if ! groups "$USER" | grep -qw docker; then
    sudo usermod -aG docker "$USER"
    echo "Added $USER to the docker group — log out/in (or run 'newgrp docker') for it to take effect."
  fi
}

main() {
  install_node_pnpm
  install_uv
  install_gh_cli
  install_docker

  log "Done"
  echo "Open a new shell (or 'source ~/.bashrc') to pick up PATH changes from this run."
}

main "$@"
