"""Regression: Stardust must not expose a gateway RPC that uploads diagnostics to Nous."""

from tui_gateway import server


def test_diagnostics_share_nous_rpc_is_not_registered():
    assert "diagnostics.share_nous" not in server._methods
