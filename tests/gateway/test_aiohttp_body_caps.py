"""Regression tests: aiohttp servers must set an explicit ``client_max_size``.

Without it, aiohttp falls back to its implicit 1 MiB default and — worse —
handlers that only check ``Content-Length`` can be bypassed entirely by
chunked transfer-encoding requests (#58536 webhook, #58902 raft lineage).
These tests pin the wiring for the three servers fixed in this follow-up:
bluebubbles, teams, and the ``stardust proxy`` server.
"""

import inspect
