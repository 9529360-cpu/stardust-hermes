# Windows helper latency

| Backend | Case | Before median ms | Before p95 ms | Failures | After median ms | After p95 ms | Failures |
|---|---|---:|---:|---:|---:|---:|---:|
| python | echo | 128.290 | 171.214 | 0 | 113.779 | 170.982 | 0 |
| python | powershell | 385.316 | 400.453 | 0 | 385.348 | 387.711 | 0 |
| python | large_output | 259.603 | 277.142 | 0 | 259.418 | 275.416 | 0 |
| python | timeout | 258.488 | 261.139 | 0 | 258.544 | 260.901 | 0 |
| rust | echo | 172.817 | 187.722 | 0 | 172.500 | 176.745 | 0 |
| rust | powershell | 387.642 | 402.945 | 0 | 387.683 | 390.468 | 0 |
| rust | large_output | 262.322 | 395.523 | 0 | 262.017 | 278.033 | 0 |
| rust | timeout | 258.752 | 261.594 | 0 | 258.812 | 261.236 | 1 |

200 runs per cell; 3,200 total. Raw timings and error indices are in `windows-latency.json` (about 30 KiB). p95 uses nearest rank. Expected timeouts (124) are successes; the one AFTER Rust timeout error is `NoSuchProcess` at zero-based repetition 178. It was not retried or discarded. Its source was not established by this benchmark.

Measured on native Windows, Python 3.11.16, rustc 1.99.1, MSVC 14.44, optimized release. Base 83e94a46e448ac4b0dbc28537fe14ee6477c9d66. Python backend is selected by unsetting STARDUST_EXEC_BACKEND; Rust uses explicit rust and the helper binary without stardust_native. Backend order alternates each repetition. Session initialization/warmup is excluded. Cases: echo, Windows PowerShell without profile, exactly 3 MiB ASCII, Python sleep with 200 ms timeout. Full non-timeout output and return codes are checked on every repetition. Small targeted test/build commands overlapped portions of the measurements; host load and Python echo drift limit causal conclusions. These are real end-to-end execute completion timings, not isolated CreateProcess timings.

Reproduce with the same interpreter and release build, isolated STARDUST_HOME/HERMES_HOME and TEMP/TMP, then run `python scripts/benchmark_exec_latency.py --phase before --output measurements.json`; rebuild the candidate and run with `--phase after` against that file. Each phase takes several minutes.

Change: serialize each protocol event into a byte vector and write its JSON plus newline together, preserving immediate flush. Regression sink measured 5,009 writes before versus one after for escaped output. Rust large-output p95 improved in this sample, but spawn median did not materially improve. Do not switch the default: Rust is not clearly faster and the timeout error prevents claiming equal stability. Python remains default. The separate drain-deadline and partial-output audit findings are untouched.
