"""End-to-end rehearsal of background work through the real desktop backend: a delegated result must
come back into the original conversation on its own, and a backend killed mid-task must, after restart,
mark the lost work "outcome unknown", keep the durable cron job, and let the conversation say so.
"""
from evals.assistant_scenarios import rehearsal, scenarios
from tests.evals.assistant_rehearsal_support import explain, failed


def test_background_research_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("background_research"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)


def test_restart_honesty_rehearsal_passes(tmp_path):
    result = rehearsal.run(scenarios.by_key("restart_honesty"), tmp_path)
    assert (result.verdict.status, failed(result)) == ("pass", []), explain(result)
