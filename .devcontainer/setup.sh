#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -qq
sudo apt-get install -y -qq \
  xvfb \
  libgtk-3-0 libnotify4 libnss3 libxss1 libxtst6 \
  xdg-utils libatspi2.0-0 libdrm2 libgbm1 libasound2t64 \
  build-essential pkg-config python3-dev

npm install --global npm@12

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/0.9.28/install.sh | sh
fi

export PATH="${HOME}/.local/bin:${PATH}"

npm ci
uv python install 3.11
uv sync --locked --python 3.11 --extra all --extra dev

cat <<'EOF'

Stardust Codespace is ready.

Desktop UI (browser VNC): forwarded port 6080
Run the real Electron desktop:
  cd apps/desktop
  npm run dev

Run the focused desktop smoke:
  cd apps/desktop
  npm run build
  xvfb-run -a --server-args="-screen 0 1280x1024x24" \
    npx playwright test e2e/stardust-shell.spec.ts --reporter=list

EOF
