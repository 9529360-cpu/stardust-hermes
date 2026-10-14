#!/usr/bin/env bash
# Process-level tests for scripts/install-stardust.sh.
# A fake curl supplies local installer fixtures and records the requested URL;
# the wrapper then executes only those fixtures. The controlled PATH and fake
# command ensure this test cannot contact an installer host.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
wrapper="$repo_root/scripts/install-stardust.sh"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/stardust-wrapper-tests.XXXXXX")"
fake_bin="$temp_root/bin"
mkdir -p "$fake_bin"
trap 'rm -rf -- "$temp_root"' EXIT

if [[ ! -f "$wrapper" ]]; then
  printf 'Missing wrapper: %s\n' "$wrapper" >&2
  exit 1
fi

cat > "$fake_bin/curl" <<'FAKE_CURL'
#!/bin/bash
set -euo pipefail
url=""
output=""
while (($#)); do
  case "$1" in
    https://*) url="$1" ;;
    -o)
      shift
      output="${1:?missing -o path}"
      ;;
  esac
  shift
done
printf '%s\n' "$url" > "$STARDUST_URL_CAPTURE"
if [[ "${STARDUST_CURL_FAIL:-0}" == 1 ]]; then
  exit 22
fi
[[ -n "$url" && -n "$output" ]]
cp -- "$STARDUST_FAKE_SOURCE" "$output"
FAKE_CURL
chmod +x "$fake_bin/curl"

write_rewritable_fixture() {
  cat > "$1" <<'FIXTURE'
#!/bin/bash
{
  printf '%s\n' \
    'git@github.com:NousResearch/hermes-agent' \
    'https://github.com/NousResearch/hermes-agent/archive/main.zip' \
    'https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh' \
    'https://hermes-agent.nousresearch.com/install.ps1' \
    'https://hermes-agent.nousresearch.com/install.sh' \
    'Hermes Agent Installer' \
    'An open source AI agent by Nous Research.' \
    'Download Hermes Agent' \
    'When running as root on Linux, Hermes installs'
} > "$STARDUST_CONTENT_CAPTURE"
if (($#)); then
  printf '%s\n' "$@" > "$STARDUST_INSTALL_CAPTURE"
else
  : > "$STARDUST_INSTALL_CAPTURE"
fi
exit 23
FIXTURE
}

expected_content="$temp_root/expected-content.txt"
cat > "$expected_content" <<'EXPECTED'
git@github.com:9529360-cpu/stardust-hermes
https://github.com/9529360-cpu/stardust-hermes/archive/main.zip
https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install.sh
https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1
https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh
Stardust Personal Assistant Installer
Personal assistant built on the Hermes Agent foundation.
Download Stardust
When running as root on Linux, Stardust installs
EXPECTED

invoke_wrapper() {
  local name="$1"
  local source="$2"
  local install_ref="$3"
  local curl_fail="$4"
  shift 4

  local url_capture="$temp_root/$name-url.txt"
  local content_capture="$temp_root/$name-content.txt"
  local install_capture="$temp_root/$name-args.txt"
  local status_capture="$temp_root/$name-status.txt"
  local output_capture="$temp_root/$name-output.txt"
  local status=0

  if (
    export PATH="$fake_bin:/usr/bin:/bin"
    export STARDUST_FAKE_SOURCE="$source"
    export STARDUST_URL_CAPTURE="$url_capture"
    export STARDUST_CONTENT_CAPTURE="$content_capture"
    export STARDUST_INSTALL_CAPTURE="$install_capture"
    unset STARDUST_INSTALL_REF STARDUST_CURL_FAIL || true
    if [[ -n "$install_ref" ]]; then export STARDUST_INSTALL_REF="$install_ref"; fi
    if [[ "$curl_fail" == 1 ]]; then export STARDUST_CURL_FAIL=1; fi
    "$wrapper" "$@"
  ) > "$output_capture" 2>&1; then
    status=0
  else
    status=$?
  fi
  printf '%s\n' "$status" > "$status_capture"

  CASE_URL_CAPTURE="$url_capture"
  CASE_CONTENT_CAPTURE="$content_capture"
  CASE_INSTALL_CAPTURE="$install_capture"
  CASE_STATUS_CAPTURE="$status_capture"
  CASE_OUTPUT_CAPTURE="$output_capture"
}

assert_equal() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [[ "$expected" != "$actual" ]]; then
    printf 'FAIL: %s\n  expected: %s\n  actual:   %s\n' "$label" "$expected" "$actual" >&2
    if [[ -n "${CASE_OUTPUT_CAPTURE:-}" && -f "$CASE_OUTPUT_CAPTURE" ]]; then cat "$CASE_OUTPUT_CAPTURE" >&2; fi
    exit 1
  fi
  printf 'OK: %s\n' "$label"
}

assert_args() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  if ! diff -u "$expected" "$actual"; then
    printf 'FAIL: %s\n' "$label" >&2
    exit 1
  fi
  printf 'OK: %s\n' "$label"
}

assert_rewrote_fixture() {
  local actual="$1"
  if ! diff -u "$expected_content" "$actual"; then
    printf 'FAIL: Stardust product URLs and user-facing installer copy are rewritten\n' >&2
    exit 1
  fi
  printf 'OK: Stardust product URLs and user-facing installer copy are rewritten\n'
}

source_fixture="$temp_root/installer-fixture.sh"
write_rewritable_fixture "$source_fixture"

expected_args="$temp_root/expected-args.txt"
empty_args="$temp_root/empty-args.txt"
: > "$empty_args"

invoke_wrapper default "$source_fixture" '' 0
assert_equal 23 "$(<"$CASE_STATUS_CAPTURE")" 'default ref preserves child exit code'
assert_equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install.sh' "$(<"$CASE_URL_CAPTURE")" 'default install source is Stardust main'
assert_rewrote_fixture "$CASE_CONTENT_CAPTURE"
assert_args 'default invocation has no installer arguments' "$empty_args" "$CASE_INSTALL_CAPTURE"

commit_args=(-Manifest manifest.json -Stage 3 -NonInteractive -Json -IncludeDesktop -Commit commit-sha -Tag v1.2.3 -Branch feature/test)
invoke_wrapper commit "$source_fixture" '' 0 "${commit_args[@]}"
assert_equal 23 "$(<"$CASE_STATUS_CAPTURE")" 'commit-pinned child exit code is preserved'
assert_equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/commit-sha/scripts/install.sh' "$(<"$CASE_URL_CAPTURE")" 'commit pin takes precedence over tag and branch'
printf '%s\n' --manifest manifest.json --stage 3 --non-interactive --json --include-desktop --commit commit-sha --tag v1.2.3 --branch feature/test > "$expected_args"
assert_args 'shell wrapper normalizes and forwards every option and value' "$expected_args" "$CASE_INSTALL_CAPTURE"
assert_rewrote_fixture "$CASE_CONTENT_CAPTURE"

tag_args=(-Tag v2.4.0 -Branch ignored-branch)
invoke_wrapper tag "$source_fixture" '' 0 "${tag_args[@]}"
assert_equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/v2.4.0/scripts/install.sh' "$(<"$CASE_URL_CAPTURE")" 'tag pin takes precedence over branch'
printf '%s\n' --tag v2.4.0 --branch ignored-branch > "$expected_args"
assert_args 'tag and branch arguments are normalized and forwarded' "$expected_args" "$CASE_INSTALL_CAPTURE"

branch_args=(-Branch release-candidate)
invoke_wrapper branch "$source_fixture" '' 0 "${branch_args[@]}"
assert_equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/release-candidate/scripts/install.sh' "$(<"$CASE_URL_CAPTURE")" 'branch ref selects the downloaded installer'
printf '%s\n' --branch release-candidate > "$expected_args"
assert_args 'branch argument is normalized and forwarded' "$expected_args" "$CASE_INSTALL_CAPTURE"

override_args=(-Commit argument-pin -NonInteractive)
invoke_wrapper override "$source_fixture" environment-pin 0 "${override_args[@]}"
assert_equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/environment-pin/scripts/install.sh' "$(<"$CASE_URL_CAPTURE")" 'STARDUST_INSTALL_REF overrides the argument pin'
printf '%s\n' --commit argument-pin --non-interactive > "$expected_args"
assert_args 'environment ref override leaves forwarded options intact' "$expected_args" "$CASE_INSTALL_CAPTURE"

leaking_fixture="$temp_root/installer-with-upstream-source.sh"
cat > "$leaking_fixture" <<'LEAK'
#!/bin/bash
: > "$STARDUST_INSTALL_CAPTURE"
printf '%s\n' 'git://github.com/NousResearch/hermes-agent'
exit 23
LEAK
invoke_wrapper leak "$leaking_fixture" '' 0
assert_equal 1 "$(<"$CASE_STATUS_CAPTURE")" 'surviving upstream product-source URL fails closed'
if [[ -e "$CASE_INSTALL_CAPTURE" ]]; then
  printf 'FAIL: wrapper executed installer content after URL enforcement failed\n' >&2
  exit 1
fi
if ! grep -Fq 'upstream product-source URL survived rewriting' "$CASE_OUTPUT_CAPTURE"; then
  printf 'FAIL: missing fail-closed diagnostic\n' >&2
  cat "$CASE_OUTPUT_CAPTURE" >&2
  exit 1
fi
printf 'OK: surviving upstream product-source URL blocks execution\n'

invoke_wrapper download-failure "$source_fixture" '' 1
assert_equal 22 "$(<"$CASE_STATUS_CAPTURE")" 'download failure stops before installer execution'
if [[ -e "$CASE_INSTALL_CAPTURE" ]]; then
  printf 'FAIL: installer executed after fake curl failed\n' >&2
  exit 1
fi
printf 'OK: failed download has no installer side effect\n'

printf '\nall Stardust shell wrapper assertions passed\n'
