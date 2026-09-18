# Stardust Backend Lab

Dedicated backend-workstation validation loop. No model/API agent is involved.

## Runtime

- `StardustBackendLabWatch`: `watchexec` watches backend source/tests and restarts the fast lane after real content changes.
- `StardustBackendLabDeep`: every 30 minutes rotates deeper regression packs.
- `StardustBackendLabDashboard`: loopback-only stage/status dashboard on `http://127.0.0.1:8765/latest.html`.
- `StardustBackendLabAllure`: Allure Report 3 real-time test dashboard on `http://127.0.0.1:8766`.
- Windows Firewall blocks remote inbound TCP/8766; localhost access remains available.

## Evidence

- Current stage/heartbeat: `D:\远程工作区\artifacts\backend-lab\current.json`.
- Latest aggregate status: `D:\远程工作区\artifacts\backend-lab\latest.json`.
- Open failures: `D:\远程工作区\artifacts\backend-lab\needs_attention.json`.
- Per-run logs/history: `D:\远程工作区\artifacts\backend-lab\runs` and `history`.
- Allure raw results: `D:\远程工作区\artifacts\backend-lab\allure-results`.
- Allure trend history: `D:\远程工作区\artifacts\backend-lab\allure\history.jsonl`.

## Lanes

Fast: changed backend Ruff checks, compute-host + assistant/memory/Todo smoke regressions, then `git diff --check`.
Deep: rotates `hosted-room -> timeline -> agent-core`; one pack per scheduled run.
Failures are pack-owned: a green fast run cannot erase an unresolved red deep pack.
Stage processes have heartbeats and bounded timeouts; timed-out process trees are terminated and recorded as evidence.

Install or refresh scheduled tasks with:
`powershell -ExecutionPolicy Bypass -File scripts\backend_lab_install.ps1`.
