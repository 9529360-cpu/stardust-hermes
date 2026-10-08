import pytest

from gateway.browser_control_broker import (
    BrowserControlBroker,
    ControllerScope,
    LaunchGrantInvalid,
    local_desktop_principal,
)


def _scope(**overrides):
    values = {
        'principal_id': 'principal-fixture',
        'profile_id': 'default',
        'session_id': 'session-fixture',
        'controller_id': 'controller-fixture',
        'browser_profile_id': 'browser-profile-fixture',
        'transport_family': 'local-api',
        'capabilities': frozenset({'browser_snapshot'}),
    }
    values.update(overrides)
    return ControllerScope(**values)


def test_bridge_grant_is_short_lived_single_use_and_scope_bound():
    now = [100.0]
    broker = BrowserControlBroker(launch_grant_ttl=30.0, clock=lambda: now[0])
    scope = _scope()
    grant = broker.mint_bridge_grant(scope)
    assert grant.expires_at == 130.0
    assert broker.consume_bridge_grant(grant.value, scope=scope) == scope
    with pytest.raises(LaunchGrantInvalid, match='unknown|consumed'):
        broker.consume_bridge_grant(grant.value)

    expired = broker.mint_bridge_grant(scope)
    now[0] = 131.0
    with pytest.raises(LaunchGrantInvalid, match='expired'):
        broker.consume_bridge_grant(expired.value)


def test_bridge_grant_scope_mismatch_does_not_consume_grant():
    broker = BrowserControlBroker()
    grant = broker.mint_bridge_grant(_scope())
    with pytest.raises(LaunchGrantInvalid, match='scope mismatch'):
        broker.consume_bridge_grant(grant.value, scope=_scope(session_id='other-session'))
    assert broker.consume_bridge_grant(grant.value, scope=_scope()) == _scope()


def test_local_desktop_principal_is_stable_and_does_not_expose_raw_values():
    value = local_desktop_principal('profile-secret', 'session-secret')
    assert value.startswith('principal:desktop:')
    assert 'profile-secret' not in value
    assert 'session-secret' not in value
    assert value == local_desktop_principal('profile-secret', 'session-secret')
