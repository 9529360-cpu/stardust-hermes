#!/bin/bash
set -euo pipefail

# Stardust-owned install entrypoint.
#
# The large cross-platform installer still lives in scripts/install.sh, but
# Stardust owns the source/recovery authority. We rewrite every repository URL
# that can select product source, then fail closed if an upstream product URL
# survived. Dependency hosts and unrelated third-party installers are left
# untouched. User-facing inherited installer copy is normalized to Stardust;
# internal Hermes paths/commands remain where compatibility still requires them.
#
# STARDUST_INSTALL_REF can override the source script ref explicitly. Otherwise
# inherit --commit / --tag / --branch from the stage-protocol invocation so a
# packaged desktop bootstrap keeps the same immutable pin as the code it will
# install. Plain interactive installs default to main.

STARDUST_REPO="9529360-cpu/stardust-hermes"

# The Tauri bootstrap owns one cross-platform stage protocol and historically
# emits PowerShell-style flags on every OS. On Unix the mature installer uses
# GNU-style long flags instead. Normalize that compatibility surface here so
# both Electron's native POSIX args and Tauri's shared protocol reach
# scripts/install.sh with the syntax it actually accepts.
NORMALIZED_ARGS=()
normalize_args() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            -Manifest) NORMALIZED_ARGS+=("--manifest") ;;
            -Stage) NORMALIZED_ARGS+=("--stage") ;;
            -NonInteractive) NORMALIZED_ARGS+=("--non-interactive") ;;
            -Json) NORMALIZED_ARGS+=("--json") ;;
            -IncludeDesktop) NORMALIZED_ARGS+=("--include-desktop") ;;
            -Commit) NORMALIZED_ARGS+=("--commit") ;;
            -Tag) NORMALIZED_ARGS+=("--tag") ;;
            -Branch) NORMALIZED_ARGS+=("--branch") ;;
            *) NORMALIZED_ARGS+=("$arg") ;;
        esac
    done
}

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

normalize_args "$@"
ARG_REF="$(resolve_ref_from_args "${NORMALIZED_ARGS[@]}")"
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
# The final replacements are presentation-only: they keep the mature installer
# implementation while preventing a fresh Stardust install from introducing
# itself as the upstream Nous product. The SOUL replacement is product behavior,
# not branding: a first run must seed the same personal-assistant contract as
# hermes_cli/default_soul.py instead of reviving the inherited Hermes persona.
sed -i.bak \
    -e 's#git@github.com:NousResearch/hermes-agent#git@github.com:9529360-cpu/stardust-hermes#g' \
    -e 's#https://github.com/NousResearch/hermes-agent#https://github.com/9529360-cpu/stardust-hermes#g' \
    -e 's#https://raw.githubusercontent.com/NousResearch/hermes-agent#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes#g' \
    -e 's#https://hermes-agent.nousresearch.com/install.ps1#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1#g' \
    -e 's#https://hermes-agent.nousresearch.com/install.sh#https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh#g' \
    -e 's#Hermes Agent Installer#Stardust Personal Assistant Installer#g' \
    -e 's#An open source AI agent by Nous Research\.#Personal assistant built on the Hermes Agent foundation.#g' \
    -e 's#Download Hermes Agent#Download Stardust#g' \
    -e 's#When running as root on Linux, Hermes installs#When running as root on Linux, Stardust installs#g' \
    -e "s#You are Hermes Agent, built by Nous Research\. Be direct:#You are Stardust, a long-lived personal AI assistant and work orchestrator. Treat each user message first as intent: if the user is asking a question, discussing an idea, or wants advice, answer directly instead of turning it into an action workflow. When the user asks you to do work, use the available tools or delegate bounded work, keep the user's context stable, and ask only for missing decisions or approvals that materially belong to them. Work that must run later, recur, or survive a restart belongs on a durable scheduler or task rail, not process-local background delegation. Never let background work steal the user's focus; report useful state and terminal outcomes instead. Be direct:#g" \
    "$TMP_INSTALLER"
rm -f "${TMP_INSTALLER}.bak"

# Supply-chain boundary: never execute an installer that can still select the
# original Hermes repository or installer endpoint as product source.
if grep -Eq 'github\.com/NousResearch/hermes-agent|raw\.githubusercontent\.com/NousResearch/hermes-agent|hermes-agent\.nousresearch\.com/install\.(sh|ps1)' "$TMP_INSTALLER"; then
    echo "Stardust installer refused to run: an upstream product-source URL survived rewriting." >&2
    exit 1
fi

chmod +x "$TMP_INSTALLER"
exec /bin/bash "$TMP_INSTALLER" "${NORMALIZED_ARGS[@]}"
