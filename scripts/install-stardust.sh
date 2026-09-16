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
# STARDUST_INSTALL_REF can override the source script ref explicitly. Otherwise
# inherit --commit / --tag / --branch from the stage-protocol invocation so a
# packaged desktop bootstrap keeps the same immutable pin as the code it will
# install. Plain interactive installs default to main.

STARDUST_REPO="9529360-cpu/stardust-hermes"

resolve_ref_from_args() {
    local commit_ref=""
    local tag_ref=""
    local branch_ref=""
    local expect=""
    local arg

    for arg in "$@"; do
        if [ -n "$expect" ]; then
            case "$expect" in
                commit) commit_ref="$arg" ;;
                tag) tag_ref="$arg" ;;
                branch) branch_ref="$arg" ;;
            esac
            expect=""
            continue
        fi

        case "$arg" in
            --commit) expect="commit" ;;
            --tag) expect="tag" ;;
            --branch) expect="branch" ;;
            --commit=*) commit_ref="${arg#--commit=}" ;;
            --tag=*) tag_ref="${arg#--tag=}" ;;
            --branch=*) branch_ref="${arg#--branch=}" ;;
        esac
    done

    if [ -n "$commit_ref" ]; then
        printf '%s' "$commit_ref"
    elif [ -n "$tag_ref" ]; then
        printf '%s' "$tag_ref"
    elif [ -n "$branch_ref" ]; then
        printf '%s' "$branch_ref"
    fi
}

ARG_REF="$(resolve_ref_from_args "$@")"
STARDUST_REF="${STARDUST_INSTALL_REF:-${ARG_REF:-main}}"
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
