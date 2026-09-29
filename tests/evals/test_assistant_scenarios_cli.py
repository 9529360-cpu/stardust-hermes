"""The exam's command line: choosing the model (rehearsal / the user's configured main model / any
OpenAI-compatible endpoint), selecting scenarios, and a rehearsal run that writes the report and
returns a meaningful exit code. The configured-model path is checked against a throwaway HERMES_HOME
through the canonical runtime resolver, never against the developer's own configuration.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evals.assistant_scenarios import cli, sandbox

REPO = Path(__file__).resolve().parents[2]


def test_model_choice_is_required():
    with pytest.raises(SystemExit) as exc:
        cli.parse_args([])
    assert exc.value.code == 2


def test_explicit_endpoint_reads_the_key_from_the_named_variable():
    args = cli.parse_args(["--base-url", "https://api.example.com/v1", "--model", "m1", "--api-key-env", "MY_KEY"])
    endpoint = cli.resolve_endpoint(args, {"MY_KEY": "sk-1"})
    assert (endpoint.base_url, endpoint.model, endpoint.api_key, endpoint.api_mode, endpoint.label) == (
        "https://api.example.com/v1", "m1", "sk-1", "chat_completions", "m1 @ api.example.com")


def test_a_named_but_missing_key_variable_is_a_usage_error():
    args = cli.parse_args(["--base-url", "https://api.example.com/v1", "--model", "m1", "--api-key-env", "MY_KEY"])
    with pytest.raises(cli.UsageError, match="MY_KEY"):
        cli.resolve_endpoint(args, {})


def test_only_selects_scenarios_in_order_and_rejects_unknown_ids():
    assert [s.id for s in cli.selected(cli.parse_args(["--rehearsal", "--only", "8,1"]))] == [1, 8]
    assert [s.id for s in cli.selected(cli.parse_args(["--rehearsal"]))] == list(range(1, 9))
    with pytest.raises(cli.UsageError, match="9"):
        cli.selected(cli.parse_args(["--rehearsal", "--only", "9"]))


def test_from_my_config_uses_the_canonical_resolver(tmp_path):
    sandbox.write_home_config(tmp_path, sandbox.ModelEndpoint(base_url="http://127.0.0.1:9/v1", model="mine"))
    code = ("import json\nfrom evals.assistant_scenarios import cli\n"
            "e = cli.resolve_endpoint(cli.parse_args(['--from-my-config']), {})\n"
            "print(json.dumps([e.base_url, e.model, e.api_key, e.api_mode]))")
    env = {**os.environ, "HERMES_HOME": str(tmp_path), "PYTHONPATH": str(REPO), sandbox.EXAM_KEY_ENV: "sk-mine"}
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["http://127.0.0.1:9/v1", "mine", "sk-mine", "chat_completions"]


def test_rehearsal_writes_the_report_and_exits_zero_when_everything_passes(tmp_path, capsys):
    assert cli.main(["--rehearsal", "--only", "6", "--out", str(tmp_path)]) == 0

    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert [(s["id"], s["passed"], s["scored"]) for s in data["scenarios"]] == [(6, 1, 1)]
    assert "总分：1/1 及格" in capsys.readouterr().out
    assert "总分：1/1 及格" in (tmp_path / "report.txt").read_text(encoding="utf-8")
