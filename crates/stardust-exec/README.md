# stardust-exec phase 0

Build from the repository root: `cargo build --release --locked -p stardust-exec`.
The bootstrap installer remains a separate workspace.

Opt in with `STARDUST_EXEC_BACKEND=rust`. The default Python execution path is
unchanged. The binary must be on PATH or in the checkout's `target/release`.
An explicit opt-in with a missing helper fails with an actionable error.
No runtime compilation, downloading, or silent retry of a command occurs.

Each UTF-8 stdin line is one sequential request:

    {"argv":["python","-c","print(123)"],"timeout_ms":5000}

Optional fields: `cwd`, `env` (complete child environment, replacing inheritance),
`stdin` (UTF-8 text). `argv` never implicitly invokes a shell. LocalEnvironment
supplies its existing sanitized environment and shell/session wrapper.

Stdout contains UTF-8 JSON lines: `started_pid`, streaming `output`, then
`exit_code`, `elapsed_ms`, and `timed_out`. Errors produce an `error` and final
exit code 1. Timing includes spawn and drain. Timeout returns 124 and terminates
the Windows job / POSIX process group. Normal exit permits detached descendants.
Windows output uses UTF-8 per line with OEM-code-page fallback; final incomplete
UTF-8 keeps replacement characters. CRLF is preserved. stdout/stderr share a pipe.

Run integration tests after building via
`scripts/run_tests.sh tests/tools/test_rust_exec.py`. CI builds and tests on
Windows, Linux, and macOS. Run `scripts/benchmark_rust_exec.py --output report.json`
with an isolated `STARDUST_HOME` to compare real LocalEnvironment calls. Phase 0
adds process/protocol overhead and does not promise a speedup; do not enable it by
default without measured benefit. Requests should come from a trusted local
caller: the helper executes arbitrary commands with the caller's privileges.
