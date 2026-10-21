# Optional Stardust native kernels

This crate is part of the root Cargo workspace. It provides a separately
installable PyO3 module; the main Python package does not require Rust.

From a source checkout with a Rust toolchain and the normal Python environment:

    uv pip install ./crates/stardust-native

Removal of `stardust-native` restores pure-Python diagnostics/redaction and the
JSONL helper adapter. No runtime compilation or download occurs. The module is
loaded lazily on the first relevant call. Do not replace it during a session.

The diagnostic kernel implements CPython difflib's no-junk SequenceMatcher ratio,
including the 200-character autojunk threshold, earliest-match tie breaking and
Unicode code points. Python retains splitting, stripping, sorting and rendering.
Surrogateescaped strings use the Python fallback.

The assignment prefilter only skips assignment regexes when an ASCII input has
none of their required secret words. Unicode and candidate-bearing text always
run the original regex/replacement policy. Vault, plugin, header, prefix, URL,
phone and other redaction passes remain authoritative in Python. This is not a
replacement redaction policy.

With the existing `STARDUST_EXEC_BACKEND=rust` opt-in, `RustProcess` uses the
in-process native child when available. It shares the helper's Windows Job / POSIX
process-group spawn and termination code, avoiding another executable launch,
JSONL encoding, and protocol parsing for each command. Without the module it uses
the existing helper. The default backend remains Python. The native adapter drains
with the existing console decoder; timeout is 124, cancellation terminates the
owned job/group, and detached output readers stop after a bounded drain grace.

The low-level NativeChild read returns None when no bytes are ready, b'' on EOF,
and bytes otherwise. Read and wait release the GIL; wait polls without holding the
state mutex so another thread can cancel it. It is an internal primitive owned by
the Python adapter.

Validation:

    cargo test --locked --workspace
    scripts/run_tests.sh tests/tools/test_native_hotspots.py tests/tools/test_rust_exec.py
    python scripts/benchmark_native_hotspots.py --output report.json
    python scripts/benchmark_native_exec.py --output exec-report.json

Benchmarks require an isolated STARDUST_HOME and assert identical outputs. They
interleave implementations to reduce drift. Native tests skip when the optional
module is absent; the three-platform Rust CI lane builds it and runs them.
