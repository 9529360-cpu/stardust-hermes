"""Regression tests for browser transaction state ownership."""

import pytest

from gateway.browser_transaction_store import BrowserTransactionStore
from gateway.browser_transactions import BrowserTransaction, BrowserTransactionStatus


def test_transaction_tracks_progress_and_artifact_references():
    tx = BrowserTransaction(
        transaction_id="tx-1",
        session_id="session-1",
        operation="file_download",
    )

    tx.advance("waiting_for_download")
    tx.attach_artifact("artifact-1")
    tx.attach_artifact("artifact-1")

    assert tx.status == BrowserTransactionStatus.RUNNING
    assert tx.current_step == "waiting_for_download"
    assert tx.artifact_ids == ["artifact-1"]


def test_transaction_store_owns_unique_runtime_state():
    store = BrowserTransactionStore()
    tx = BrowserTransaction(
        transaction_id="tx-unique",
        session_id="session-1",
        operation="upload",
    )

    assert store.create(tx) is tx
    assert store.get("tx-unique") is tx
    assert store.count() == 1

    with pytest.raises(ValueError):
        store.create(tx)

    assert store.remove("tx-unique") is tx
    assert store.count() == 0


def test_failed_transaction_keeps_recovery_error():
    tx = BrowserTransaction(
        transaction_id="tx-failed",
        session_id="session-1",
        operation="upload",
    )

    tx.fail("controller disconnected")

    assert tx.status == BrowserTransactionStatus.FAILED
    assert tx.last_error == "controller disconnected"
