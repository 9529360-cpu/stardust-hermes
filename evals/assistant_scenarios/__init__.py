"""Stardust assistant-scenario exam: the eight acceptance scenarios of mission #16, run live.

Each scenario drives the desktop backend (``tui_gateway``) over its JSON-RPC wire in an isolated
HERMES_HOME + workspace, then grades what actually happened (tool events, approval requests, files
on disk, the cron store, the delegation ledger). See ``README.md`` for the contract and how to run it.
"""
