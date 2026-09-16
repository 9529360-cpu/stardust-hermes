#!/bin/bash
set -euo pipefail

# Stardust-owned install entrypoint.
#
# The large cross-platform installer still lives in scripts/install.sh, but
# Stardust owns the source/recovery authority. We rewrite every repository URL
# that can select product source, then fail closed if an upstream product URL
# survived. Dependency hosts and unrelated third-party installers are left
# untouched.
#
# STARDUST_INSTALL_REF is intentionally overridable for release-candidate smoke
# tests; normal installs stay on main.

STARDUST_REPO="9529360-cpu/stardust-hermes"
STARDUST_REF="${STARDUST_INSTALL_REF:-main}"
INSTALLER_URL="https://raw.githubusercontent.com/${STARDUST_REPO}/${STARDUST_REF}/scripts/install.sh"

if ! command -v curl >/dev/null 2>&1; then
    echo "Stardust installer requires curl to bootstrap the product installer." >&2
    exit 1
fi

TMP_INSTALLER="$(mktemp 2>/dev/null || printf '/tmp/stardust-install.%s.sh' "$$")"
cleanup() {
    rm -f "$TMP_INSTALLER" "${TMP_INSTALLER}.bak"
}
trap cleanup EXIT HUP INT TERM

curl -fsSL --retry 3 --retry-delay 2 "$INSTALLER_URL" -o "$TMP_INSTALLER"

# Replace the whole upstream repository prefix, not only the .git form. This
# also catches archive/zip recovery URLs and future paths beneath the repo.
sed -i.bak \
    -e 's#git@github.com:NousResearch/hermes-agent#git@github.com:9529360-cpu/stardust-hermes#g' \
    -e 's#https://github.com/NousResearch/hermes-agent#https://github.com/9529360-cpu/stardust-hermes#g' \
    -e 's#https://raw.githubusercontent.com/NousResearch/hermes-agent#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes#g' \
    -e 's#https://hermes-agent.nousresearch.com/install.ps1#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1#g' \
    -e 's#https://hermes-agent.nousresearch.com/install.sh#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh#g' \
    "$TMP_INSTALLER"
rm -f "${TMP_INSTALLER}.bak"

# Supply-chain boundary: never execute an installer that can still select the
# original Hermes repository or installer endpoint as product source.
if grep -Eq 'github\.com/NousResearch/hermes-agent|raw\.githubusercontent\.com/NousResearch/hermes-agent|hermes-agent\.nousresearch\.com/install\.(sh|ps1)' "$TMP_INSTALLER"; then
    echo "Stardust installer refused to run: an upstream product-source URL survived rewriting." >&2
    exit 1
fi

chmod +x "$TMP_INSTALLER"
exec /bin/bash "$TMP_INSTALLER" "$@"
