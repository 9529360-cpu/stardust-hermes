"""End-to-end rehearsal of the durable-work and approval scenarios through the real desktop backend:
the cron store is read back from disk and the approval is answered on the wire, so a job that was only
promised (never created) fails and a denied destructive command leaves the repository intact.
"""
from evals.assistant_scenarios import rehearsal, scenarios
from evals.assistant_scenarios.fake_model import Reply
from tests.evals.assistant_rehearsal_support import explain, failed


def test_daily_mail_watch_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("daily_mail_watch"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)


def test_daily_mail_watch_fails_when_the_job_is_only_promised(tmp_path):
    result = rehearsal.run(scenarios.by_key("daily_mail_watch"), tmp_path,
                           script=lambda req: Reply(text="好的，以后我每天早上都会帮你看邮件。"))
    assert failed(result) == ["durable_job"], explain(result)


def test_dangerous_command_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("dangerous_command"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)
