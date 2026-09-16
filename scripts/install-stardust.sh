#!/bin/bash
set -euo pipefail

# Stardust-owned install entrypoint.
#
# Keep the mature Hermes installer as the implementation, but rewrite the few
# source-authority constants before execution so a fresh install, repair, or
# re-run can never silently clone NousResearch/hermes-agent over this product.
# STARDUST_INSTALL_REF is intentionally overridable for release-candidate smoke
# tests; normal users stay on main.

STARDUST_REPO="9529360-cpu/stardust-hermes"
STARDUST_REF="${STARDUST_INSTALL_REF:-main}"
INSTALLER_URL="https://raw.githubusercontent.com/${STARDUST_REPO}/${STARDUST_REF}/scripts/install.sh"

if ! command -v curl >/dev/null 2>&1; then
    echo "Stardust installer requires curl to bootstrap the product installer." >&2
    exit 1
fi

TMP_INSTALLER="$(mktemp 2>/dev/null || printf '/tmp/stardust-install.%s.sh' "$$")"
cleanup() {
    rm -f "$TMP_INSTALLER"
}
trap cleanup EXIT HUP INT TERM

curl -fsSL --retry 3 --retry-delay 2 "$INSTALLER_URL" -o "$TMP_INSTALLER"

# Only product-source / recovery URLs are rewritten. Dependency hosts and
# third-party tool installers remain untouched.
sed -i.bak \
    -e 's#git@github.com:NousResearch/hermes-agent.git#git@github.com:9529360-cpu/stardust-hermes.git#g' \
    -e 's#https://github.com/NousResearch/hermes-agent.git#https://github.com/9529360-cpu/stardust-hermes.git#g' \
    -e 's#https://hermes-agent.nousresearch.com/install.ps1#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1#g' \
    -e 's#https://hermes-agent.nousresearch.com/install.sh#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh#g' \
    "$TMP_INSTALLER"
rm -f "${TMP_INSTALLER}.bak"
chmod +x "$TMP_INSTALLER"

exec /bin/bash "$TMP_INSTALLER" "$@"
