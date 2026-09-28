"""In-memory browser transaction owner.

Keeps transaction lifecycle separate from browser controllers and artifact bytes.
The controller owns execution, ArtifactStore owns files, this store owns workflow
state lookup for one runtime.
"""

from __future__ import annotations

import threading
from typing import Optional

from gateway.browser_transactions import BrowserTransaction


class BrowserTransactionStore:
    """Thread-safe owner for active browser workflow state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._transactions: dict[str, BrowserTransaction] = {}

    def create(self, transaction: BrowserTransaction) -> BrowserTransaction:
        """Register a new transaction; duplicate ids are rejected."""
        with self._lock:
            if transaction.transaction_id in self._transactions:
                raise ValueError(f"transaction already exists: {transaction.transaction_id}")
            self._transactions[transaction.transaction_id] = transaction
        return transaction

    def get(self, transaction_id: str) -> Optional[BrowserTransaction]:
        """Return a live transaction reference or None."""
        with self._lock:
            return self._transactions.get(transaction_id)

    def remove(self, transaction_id: str) -> Optional[BrowserTransaction]:
        """Remove completed/failed workflow state when the owner decides it is safe."""
        with self._lock:
            return self._transactions.pop(transaction_id, None)

    def count(self) -> int:
        with self._lock:
            return len(self._transactions)


_GLOBAL_BROWSER_TRANSACTION_STORE = BrowserTransactionStore()


def get_browser_transaction_store() -> BrowserTransactionStore:
    return _GLOBAL_BROWSER_TRANSACTION_STORE
