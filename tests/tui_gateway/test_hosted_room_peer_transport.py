"""Peer transport tests for hosted-room member turns."""

from __future__ import annotations

from typing import Any

from gateway.hosted_room_driver import TaskIdentity
from tui_gateway.hosted_room_driver import HostedRoomBinding, ROOM_SESSION_SOURCE
from tui_gateway.hosted_room_peer_transport import (
    FailoverHostedRoomPeerClient,
    PeerHostedRoomTransport,
    PeerMemberRoute,
    RoomLinkCandidate,
)
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPError


BINDING = HostedRoomBinding("room-1", "gateway-home", 2)
ROUTE = PeerMemberRoute(
    home_install_id="install-home",
    member_id="member-reviewer",
    target_install_id="install-peer",
    target_profile="reviewer",
    capability_digest="a" * 64,
    execution_policy_digest="b" * 64,
    cancellation_scope_id="cancel-1",
    trace_id="trace-1",
    grant="signed-room-grant",
)


class FakePeerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.session = {"session_id": "group-session"}
        self.messages = []
        self.active = False
        self.task_id = None

    def bind_receipt_store(self, db_path):
        self.calls.append(("bind_receipt_store", {"db_path": db_path}))

    def bind_observation(self, **kwargs):
        self.calls.append(("bind_observation", kwargs))

    def prepare(self, **kwargs):
        self.calls.append(("prepare", kwargs))
        return (
            self.session
            if kwargs["create"] or kwargs.get("expected_session_id")
            else None
        )

    def recover_dispatch(self, **kwargs):
        self.calls.append(("recover_dispatch", kwargs))
        dispatch = kwargs["dispatch"]
        return {
            "status": "accepted",
            "task_id": dispatch["task_id"],
            "execution_generation": dispatch["execution_generation"],
        }

    def dispatch(self, **kwargs):
        self.calls.append(("dispatch", kwargs))
        dispatch = kwargs["dispatch"]
        self.active = True
        self.task_id = dispatch["task_id"]
        return {"status": "accepted", "task_id": self.task_id}

    def history(self, **kwargs):
        self.calls.append(("history", kwargs))
        return list(self.messages)

    def status(self, **kwargs):
        self.calls.append(("status", kwargs))
        return {"active": self.active, "task_id": self.task_id}

    def stop(self, **kwargs):
        self.calls.append(("stop", kwargs))
        self.active = False
        return {"status": "cancelled", "task_id": self.task_id}

    def stop_receipt(self, **kwargs):
        self.calls.append(("stop_receipt", kwargs))
        return {
            "status": "cancelled",
            "task_id": kwargs["task_id"],
            "execution_generation": kwargs["execution_generation"],
        }

    def approve_receipt(self, **kwargs):
        self.calls.append(("approve_receipt", kwargs))
        return {"resolved": 1}

    def refresh_grant(self, **kwargs):
        self.calls.append(("refresh_grant", kwargs))
        return {"grant": "refreshed-grant"}

    def revoke_grant(self, **kwargs):
        self.calls.append(("revoke_grant", kwargs))
        return {"revoked": True}


class FailingPeerClient(FakePeerClient):
    def __init__(self, *, method, retryable=True, not_admitted=False):
        super().__init__()
        self.method = method
        self.error = PeerRunsHTTPError(
            f"{method} failed",
            retryable=retryable,
            ambiguous=method == "dispatch" and not not_admitted,
            not_admitted=not_admitted,
        )

    def prepare(self, **kwargs):
        if self.method == "prepare":
            raise self.error
        return super().prepare(**kwargs)

    def recover_dispatch(self, **kwargs):
        if self.method == "recover_dispatch":
            self.calls.append(("recover_dispatch", kwargs))
            raise self.error
        return super().recover_dispatch(**kwargs)

    def dispatch(self, **kwargs):
        if self.method == "dispatch":
            self.calls.append(("dispatch", kwargs))
            raise self.error
        return super().dispatch(**kwargs)

    def status(self, **kwargs):
        if self.method == "status":
            raise self.error
        return super().status(**kwargs)

    def stop_receipt(self, **kwargs):
        if self.method == "stop_receipt":
            raise self.error
        return super().stop_receipt(**kwargs)


def _transport(client=None, *, source_event_seq=1):
    return PeerHostedRoomTransport(
        binding=BINDING,
        route=ROUTE,
        client=client or FakePeerClient(),
        source_event_seq=source_event_seq,
    )


def test_peer_transport_prepares_group_session_not_canonical_bot_chat():
    client = FakePeerClient()
    transport = _transport(client)
    assert (
        transport.resolve_exact(
            profile="reviewer",
            title="Group: room-1",
            source=ROOM_SESSION_SOURCE,
        )
        is None
    )
    created = transport.create(
        profile="reviewer",
        title="Group: room-1",
        source=ROOM_SESSION_SOURCE,
    )
    assert created["session_id"] == "group-session"
    prepare = [params for method, params in client.calls if method == "prepare"]
    assert all(params["room_id"] == "room-1" for params in prepare)
    assert all(params["source"] == "bot_room" for params in prepare)


def test_peer_transport_dispatches_full_fenced_coordinates_and_exact_stop():
    client = FakePeerClient()
    transport = _transport(client)
    transport.create(
        profile="reviewer",
        title="Group: room-1",
        source=ROOM_SESSION_SOURCE,
    )
    terminal = []
    task = TaskIdentity("room-1", "task-1", "thread-1", "turn-1")
    result = transport.submit(
        profile="reviewer",
        session_id="group-session",
        prompt="Review this change.",
        source=ROOM_SESSION_SOURCE,
        task=task,
        execution_generation=3,
        on_terminal=terminal.append,
    )
    assert result["status"] == "accepted"
    dispatch = next(params for method, params in client.calls if method == "dispatch")
    assert dispatch["dispatch"]["authority_epoch"] == 2
    assert dispatch["dispatch"]["execution_generation"] == 3
    assert dispatch["dispatch"]["target_profile"] == "reviewer"
    assert dispatch["dispatch"]["capability_digest"] == "a" * 64
    assert terminal == []

    assert (
        transport.interrupt(
            profile="reviewer",
            session_id="group-session",
            source=ROOM_SESSION_SOURCE,
            expected_task_id="other-task",
            expected_execution_generation=3,
        )
        is None
    )
    assert (
        transport.interrupt(
            profile="reviewer",
            session_id="group-session",
            source=ROOM_SESSION_SOURCE,
            expected_task_id="task-1",
            expected_execution_generation=2,
        )
        is None
    )
    stopped = transport.interrupt(
        profile="reviewer",
        session_id="group-session",
        source=ROOM_SESSION_SOURCE,
        expected_task_id="task-1",
        expected_execution_generation=3,
    )
    assert stopped["status"] == "cancelled"
    assert len([call for call in client.calls if call[0] == "stop"]) == 1


def test_peer_transport_carries_each_turns_real_source_event_sequence():
    observed = []
    for index, source_event_seq in enumerate((7, 42), start=1):
        client = FakePeerClient()
        transport = _transport(client, source_event_seq=source_event_seq)
        transport.create(
            profile="reviewer",
            title="Group: room-1",
            source=ROOM_SESSION_SOURCE,
        )
        transport.submit(
            profile="reviewer",
            session_id="group-session",
            prompt=f"Turn {index}",
            source=ROOM_SESSION_SOURCE,
            task=TaskIdentity("room-1", f"task-{index}", "thread-1", f"turn-{index}"),
            execution_generation=1,
            on_terminal=lambda _receipt: None,
        )
        dispatch = next(
            params for method, params in client.calls if method == "dispatch"
        )
        observed.append(dispatch["dispatch"]["source_event_seq"])
    assert observed == [7, 42]


def test_peer_transport_rejects_profile_source_and_room_title_mismatch():
    transport = _transport()
    for kwargs in (
        {"profile": "other", "title": "Group: room-1", "source": "bot_room"},
        {"profile": "reviewer", "title": "Bot Chat", "source": "bot_room"},
        {"profile": "reviewer", "title": "Group: room-1", "source": "cli"},
    ):
        try:
            transport.resolve_exact(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"mismatch was accepted: {kwargs}")


def test_roomlink_falls_back_to_relay_on_retryable_prepare_failure():
    direct = FailingPeerClient(method="prepare")
    relay = FakePeerClient()
    client = FailoverHostedRoomPeerClient([
        RoomLinkCandidate("direct", "direct", "install-peer", direct),
        RoomLinkCandidate("relay", "relay", "install-peer", relay),
    ])

    session = client.prepare(
        room_id="room-1",
        profile="reviewer",
        source="bot_room",
        grant="grant",
        create=True,
    )

    assert session["session_id"] == "group-session"
    assert client.active_link.name == "relay"


def test_roomlink_never_falls_back_after_ambiguous_direct_failure():
    direct = FailingPeerClient(method="dispatch")
    relay = FakePeerClient()
    client = FailoverHostedRoomPeerClient([
        RoomLinkCandidate("direct", "direct", "install-peer", direct),
        RoomLinkCandidate("relay", "relay", "install-peer", relay),
    ])
    dispatch = {"task_id": "task-1", "execution_generation": 1}

    try:
        client.dispatch(dispatch=dispatch, grant="grant")
    except PeerRunsHTTPError as exc:
        assert exc.ambiguous is True
    else:
        raise AssertionError("ambiguous dispatch was automatically replayed")

    assert direct.calls[0][1]["dispatch"] is dispatch
    assert relay.calls == []
    assert client.active_link.name == "direct"


def test_roomlink_falls_back_after_proven_not_admitted_direct_failure():
    direct = FailingPeerClient(method="dispatch", not_admitted=True)
    relay = FakePeerClient()
    client = FailoverHostedRoomPeerClient([
        RoomLinkCandidate("direct", "direct", "install-peer", direct),
        RoomLinkCandidate("relay", "relay", "install-peer", relay),
    ])
    dispatch = {"task_id": "task-1", "execution_generation": 1}

    result = client.dispatch(dispatch=dispatch, grant="grant")

    assert result["status"] == "accepted"
    assert direct.calls[0][1]["dispatch"] is dispatch
    assert relay.calls[0][1]["dispatch"] is dispatch
    assert client.active_link.name == "relay"


def test_roomlink_exposes_service_control_methods_without_losing_scope():
    client_impl = FakePeerClient()
    client = FailoverHostedRoomPeerClient([
        RoomLinkCandidate("direct", "direct", "install-peer", client_impl),
    ])

    approved = client.approve_receipt(
        task_id="task-1", execution_generation=3, request_id="approval-1",
        choice="once", grant="grant",
    )
    refreshed = client.refresh_grant(
        grant="grant", capability_digest="a" * 64, execution_policy_digest="b" * 64,
    )
    revoked = client.revoke_grant(grant="grant")

    assert approved == {"resolved": 1}
    assert refreshed == {"grant": "refreshed-grant"}
    assert revoked == {"revoked": True}
    assert [method for method, _params in client_impl.calls] == [
        "approve_receipt", "refresh_grant", "revoke_grant",
    ]


def test_roomlink_restart_recovery_binds_all_links_and_fails_over_recovery_dispatch():
    """Every link needs the same durable receipt/observation identity before recovery can switch routes."""
    direct = FailingPeerClient(method="recover_dispatch")
    relay = FakePeerClient()
    client = FailoverHostedRoomPeerClient([
        RoomLinkCandidate("direct", "direct", "install-peer", direct),
        RoomLinkCandidate("relay", "relay", "install-peer", relay),
    ])
    dispatch = {"task_id": "task-1", "execution_generation": 3}

    client.bind_receipt_store("state.db")
    client.bind_observation(task_id="task-1", execution_generation=3)
    recovered = client.recover_dispatch(dispatch=dispatch, grant="grant")

    assert recovered == {
        "status": "accepted", "task_id": "task-1", "execution_generation": 3,
    }
    for candidate in (direct, relay):
        assert ("bind_receipt_store", {"db_path": "state.db"}) in candidate.calls
        assert (
            "bind_observation", {"task_id": "task-1", "execution_generation": 3}
        ) in candidate.calls
    assert client.active_link.name == "relay"
    assert [method for method, _params in relay.calls].count("recover_dispatch") == 1


def test_roomlink_recovery_stop_receipt_fails_over_under_exact_generation():
    """After restart there is no dispatch object; exact Stop receipt still needs RoomLink failover."""
    direct = FailingPeerClient(method="stop_receipt")
    relay = FakePeerClient()
    client = FailoverHostedRoomPeerClient([
        RoomLinkCandidate("direct", "direct", "install-peer", direct),
        RoomLinkCandidate("relay", "relay", "install-peer", relay),
    ])
    transport = PeerHostedRoomTransport(
        binding=BINDING,
        route=ROUTE,
        client=client,
        task_id="task-1",
        execution_generation=3,
    )

    stopped = transport.interrupt(
        profile="reviewer",
        session_id="group-session",
        source=ROOM_SESSION_SOURCE,
        expected_task_id="task-1",
        expected_execution_generation=3,
    )

    assert stopped == {
        "status": "cancelled", "task_id": "task-1", "execution_generation": 3,
    }
    assert client.active_link.name == "relay"
    assert [method for method, _params in relay.calls] == ["stop_receipt"]


def test_roomlink_never_falls_back_after_nonretryable_rejection():
    rejected = FailingPeerClient(method="prepare", retryable=False)
    relay = FakePeerClient()
    client = FailoverHostedRoomPeerClient([
        RoomLinkCandidate("direct", "direct", "install-peer", rejected),
        RoomLinkCandidate("relay", "relay", "install-peer", relay),
    ])

    try:
        client.prepare(
            room_id="room-1",
            profile="reviewer",
            source="bot_room",
            grant="grant",
            create=True,
        )
    except PeerRunsHTTPError:
        pass
    else:
        raise AssertionError("nonretryable rejection was silently bypassed")
    assert relay.calls == []


def test_roomlink_reprobes_and_upgrades_back_to_primary_after_cooldown():
    now = [0.0]
    direct = FailingPeerClient(method="prepare")
    relay = FakePeerClient()
    client = FailoverHostedRoomPeerClient(
        [
            RoomLinkCandidate("direct", "direct", "install-peer", direct),
            RoomLinkCandidate("relay", "relay", "install-peer", relay),
        ],
        reprobe_interval_seconds=30,
        clock=lambda: now[0],
    )
    client.prepare(
        room_id="room-1",
        profile="reviewer",
        source="bot_room",
        grant="grant",
        create=True,
    )
    assert client.active_link.name == "relay"

    direct.method = "none"
    now[0] = 10
    client.prepare(
        room_id="room-1",
        profile="reviewer",
        source="bot_room",
        grant="grant",
        create=True,
    )
    assert client.active_link.name == "relay"

    now[0] = 31
    client.prepare(
        room_id="room-1",
        profile="reviewer",
        source="bot_room",
        grant="grant",
        create=True,
    )
    assert client.active_link.name == "direct"
