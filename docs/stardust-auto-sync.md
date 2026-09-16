# Stardust automatic synchronization

A Windows installation may opt into `scripts/install-stardust-auto-sync.ps1`.
The scheduled task trusts only `9529360-cpu/stardust-hermes` and only the
`main` branch. It accepts fast-forward updates, validates and packages them
before activation, and preserves the last working desktop on failure.

Runtime configuration, credentials, conversations, memory, logs, and databases
live outside the Git checkout and are never synchronized.
