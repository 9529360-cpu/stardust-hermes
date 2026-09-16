"""Regression coverage for truthful E-stop resume outcomes across CLI and Gateway."""

import asyncio


class _FakeSentinel:
    def __init__(self, name: str, *, exists: bool = True, unlink_error: Exception | None = None,
                 stat_error: Exception | None = None):
        self.name = name
        self.present = exists
        self.unlink_error = unlink_error
        self.stat_error = stat_error

    def exists(self) -> bool:
        if self.stat_error is not None:
            raise self.stat_error
        return self.present

    def unlink(self) -> None:
        if self.unlink_error is not None:
            raise self.unlink_error
        if not self.present:
            raise FileNotFoundError(self.name)
        self.present = False

    def __str__(self) -> str:
        return self.name


def test_disengage_result_requires_every_visible_sentinel_to_clear(monkeypatch):
    from agent import estop

    profile = _FakeSentinel("profile/ESTOP")
    fleet = _FakeSentinel("fleet/ESTOP", unlink_error=PermissionError("read-only"))
    monkeypatch.setattr(estop, "_candidate_sentinel_paths", lambda: [profile, fleet])

    result = estop.disengage_result()

    assert result.had_sentinel is True
    assert result.resumed is False
    assert result.incomplete is True
    assert profile.present is False
    assert fleet.present is True
    assert result.removed_paths == (profile,)
    assert fleet in result.failed_paths
    assert result.remaining_paths == (fleet,)


def test_disengage_result_is_fail_safe_on_stat_error(monkeypatch):
    from agent import estop

    unreadable = _FakeSentinel("fleet/ESTOP", stat_error=OSError("I/O error"))
    monkeypatch.setattr(estop, "_candidate_sentinel_paths", lambda: [unreadable])

    result = estop.disengage_result()

    assert result.had_sentinel is True
    assert result.resumed is False
    assert result.incomplete is True
    assert result.remaining_paths == (unreadable,)


def test_disengage_result_distinguishes_not_paused_from_success(monkeypatch):
    from agent import estop

    profile = _FakeSentinel("profile/ESTOP", exists=False)
    fleet = _FakeSentinel("fleet/ESTOP", exists=False)
    monkeypatch.setattr(estop, "_candidate_sentinel_paths", lambda: [profile, fleet])

    result = estop.disengage_result()

    assert result.had_sentinel is False
    assert result.resumed is False
    assert result.incomplete is False


def test_disengage_bool_compatibility_only_reports_complete_success(monkeypatch):
    from agent import estop

    profile = _FakeSentinel("profile/ESTOP")
    fleet = _FakeSentinel("fleet/ESTOP")
    monkeypatch.setattr(estop, "_candidate_sentinel_paths", lambda: [profile, fleet])

    assert estop.disengage() is True
    assert profile.present is False
    assert fleet.present is False


def test_cli_resume_returns_failure_when_pause_remains(monkeypatch, capsys):
    from agent import estop
    from hermes_cli.subcommands.pause import cmd_resume

    blocked = _FakeSentinel("fleet/ESTOP")
    result = estop.DisengageResult(
        had_sentinel=True,
        removed_paths=(),
        failed_paths=(blocked,),
        remaining_paths=(blocked,),
    )
    monkeypatch.setattr(estop, "disengage_result", lambda: result)

    assert cmd_resume(object()) == 1
    output = capsys.readouterr().out.lower()
    assert "resume incomplete" in output
    assert "still paused" in output
    assert "fleet/estop" in output


class _PauseEvent:
    def __init__(self, args: str):
        self._args = args

    def get_command_args(self) -> str:
        return self._args


def test_gateway_pause_off_reports_incomplete_resume(monkeypatch):
    from agent import estop
    from gateway.run_busy import GatewayBusySessionMixin

    blocked = _FakeSentinel("fleet/ESTOP")
    result = estop.DisengageResult(
        had_sentinel=True,
        removed_paths=(),
        failed_paths=(blocked,),
        remaining_paths=(blocked,),
    )
    monkeypatch.setattr(estop, "disengage_result", lambda: result)

    runner = object.__new__(GatewayBusySessionMixin)
    reply = asyncio.run(runner._handle_pause_command(_PauseEvent("off")))

    assert "resume incomplete" in reply.lower()
    assert "still paused" in reply.lower()
