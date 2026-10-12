"""Tests for the market-brief skill: the SKILL.md contract and the stocks fields it reads.

Index levels come from the stocks skill and the PDF from the pdf skill's pdf_markdown.py, so these
tests check what SKILL.md tells the agent to run, that those paths resolve in an installed skills
folder, and the stocks output the brief relies on. No test calls Yahoo Finance.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent.skill_preprocessing import substitute_template_vars

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "productivity" / "market-brief"
SKILL_PATH = SKILL_DIR / "SKILL.md"
STOCKS_CLIENT = REPO_ROOT / "optional-skills" / "finance" / "stocks" / "scripts" / "stocks_client.py"


def _frontmatter_and_body(text=None):
    content = SKILL_PATH.read_text(encoding="utf-8") if text is None else text
    assert content.startswith("---")
    match = re.search(r"\n---\s*\n", content[3:])
    assert match, "frontmatter must close with ---"
    frontmatter = yaml.safe_load(content[3 : match.start() + 3])
    return frontmatter, content[match.end() + 3 :]


def _command_path(text, script_name):
    """The quoted path that the text gives for one script in a `python "..."` command."""
    match = re.search(r'python "([^"]*' + re.escape(script_name) + r')"', text)
    assert match, f"no `python \"...{script_name}\"` command in the text"
    return match.group(1)


class TestSkillContract:
    def test_frontmatter_has_required_fields(self):
        fm, _ = _frontmatter_and_body()
        for field in ("name", "description", "version", "author", "license", "platforms"):
            assert field in fm, f"missing frontmatter field: {field}"
        assert fm["name"] == SKILL_DIR.name
        assert {"linux", "macos", "windows"} <= set(fm["platforms"])

    def test_description_meets_authoring_hardline(self):
        fm, _ = _frontmatter_and_body()
        desc = fm["description"]
        assert len(desc) <= 60, f"description is {len(desc)} chars; hardline is 60"
        assert desc.endswith(".")

    def test_related_skills_resolve_in_repo(self):
        fm, _ = _frontmatter_and_body()
        for name in fm["metadata"]["hermes"]["related_skills"]:
            hits = [
                path
                for tree in ("skills", "optional-skills")
                for path in (REPO_ROOT / tree).rglob("SKILL.md")
                if path.parent.name == name
            ]
            assert hits, f"related_skills entry does not resolve in-repo: {name}"

    def test_sections_follow_the_modern_order(self):
        _, body = _frontmatter_and_body()
        sections = [
            "# Market Brief Skill",
            "## When to Use",
            "## Prerequisites",
            "## How to Run",
            "## Quick Reference",
            "## Procedure",
            "## Pitfalls",
            "## Verification",
        ]
        positions = [body.index(section) for section in sections]
        assert positions == sorted(positions), "sections are out of order"

    def test_outline_headings_are_in_fixed_order(self):
        _, body = _frontmatter_and_body()
        outline = ["# 全球股市日报", "## 主要指数", "## 主要异动", "## 宏观要闻", "## 资料来源"]
        positions = [body.index(heading) for heading in outline]
        assert positions == sorted(positions)

    def test_trigger_wording_and_file_naming_are_kept(self):
        _, body = _frontmatter_and_body()
        for phrase in ('"今天全球股市怎么样"', '"股市日报"', '"global market brief"', "`global-market-brief`"):
            assert phrase in body, f"trigger wording missing: {phrase}"
        assert "YYYY-MM-DD-market-brief.md" in body
        assert "YYYY-MM-DD-market-brief.pdf" in body
        assert "workspace/reports" in body

    def test_unknown_figures_rule_and_three_publisher_rule_are_kept(self):
        _, body = _frontmatter_and_body()
        assert "未取到" in body
        assert "Never invent a number" in body
        assert "`web_search`" in body and "`web_extract`" in body
        assert "at least three different publishers" in body.lower()
        assert "no buy or sell advice and no trades" in body

    def test_skill_ships_no_code_and_never_mentions_reportlab(self):
        _, body = _frontmatter_and_body()
        assert "reportlab" not in body.lower()
        assert not (SKILL_DIR / "scripts" / "make_brief_pdf.py").exists()
        assert not list(SKILL_DIR.rglob("*.py"))


class TestSiblingCommands:
    def test_index_levels_come_from_the_stocks_quote_command(self):
        _, body = _frontmatter_and_body()
        assert re.search(r'python "\$\{STARDUST_SKILL_DIR\}/[^"]*stocks_client\.py" quote ', body)
        assert "Yahoo Finance (stocks skill)" in body
        assert _command_path(body, "stocks_client.py").endswith("finance/stocks/scripts/stocks_client.py")
        assert STOCKS_CLIENT.is_file()

    def test_pdf_is_rendered_by_the_pdf_skill_with_the_contract_flags(self):
        _, body = _frontmatter_and_body()
        command = next(
            line for line in body.splitlines()
            if "pdf_markdown.py" in line and line.lstrip().startswith("python")
        )
        assert command.index("--input") < command.index("--output")
        assert _command_path(body, "pdf_markdown.py").endswith("pdf/scripts/pdf_markdown.py")

    def test_paths_use_the_runtime_token_only(self):
        _, body = _frontmatter_and_body()
        assert "${STARDUST_SKILL_DIR}" in body
        assert not re.search(r"(?<!STARDUST_)SKILL_DIR", body)

    def test_reports_folder_command_creates_the_folder_under_the_stardust_home(self, tmp_path):
        _, body = _frontmatter_and_body()
        match = re.search(r'^python -c "(.+)"$', body, re.M)
        assert match, "How to Run must give the reports-folder command"
        home = tmp_path / "home"
        env = {key: value for key, value in os.environ.items() if key != "HERMES_HOME"}
        env["STARDUST_HOME"] = str(home)
        done = subprocess.run(
            [sys.executable, "-c", match.group(1)],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
        )
        assert done.returncode == 0, done.stderr
        printed = Path(done.stdout.strip().splitlines()[-1])
        assert printed == home / "workspace" / "reports"
        assert printed.is_dir()


class TestInstalledLayout:
    def test_documented_paths_resolve_after_expansion(self, tmp_path):
        skills = tmp_path / "skills"
        brief = skills / "productivity" / "market-brief"
        brief.mkdir(parents=True)
        shutil.copy(SKILL_PATH, brief / "SKILL.md")
        stocks_dir = skills / "finance" / "stocks" / "scripts"
        stocks_dir.mkdir(parents=True)
        shutil.copy(STOCKS_CLIENT, stocks_dir / "stocks_client.py")
        (skills / "productivity" / "pdf" / "scripts").mkdir(parents=True)

        expanded = substitute_template_vars(SKILL_PATH.read_text(encoding="utf-8"), brief, "test-session")
        assert "${STARDUST_SKILL_DIR}" not in expanded

        stocks_cmd = Path(_command_path(expanded, "stocks_client.py"))
        assert stocks_cmd.resolve() == (stocks_dir / "stocks_client.py").resolve()
        help_run = subprocess.run(
            [sys.executable, str(stocks_cmd), "--help"], capture_output=True, text=True, timeout=60,
        )
        assert help_run.returncode == 0, help_run.stderr

        # pdf_markdown.py is added by the pdf skill; this checks where the path points only.
        pdf_cmd = Path(_command_path(expanded, "pdf_markdown.py"))
        assert pdf_cmd.resolve() == (skills / "productivity" / "pdf" / "scripts" / "pdf_markdown.py").resolve()


@pytest.fixture
def stocks_client():
    """Load the stocks client as a module, without any network access at import time."""
    name = "market_brief_stocks_client_under_test"
    spec = importlib.util.spec_from_file_location(name, STOCKS_CLIENT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


class TestStocksQuoteFields:
    def test_level_and_day_change_come_back_as_printed_strings(self, stocks_client):
        chart = {"chart": {"result": [{"meta": {
            "regularMarketPrice": 5000.0, "previousClose": 4950.0, "chartPreviousClose": 4950.0,
            "currency": "USD", "shortName": "S&P 500",
        }}]}}
        quote = stocks_client.extract_quote_from_chart("^GSPC", chart)
        assert quote["symbol"] == "^GSPC"
        assert quote["price"] == "5000.00"
        assert quote["change_pct"] == "1.01%"
        assert not quote["change_pct"].startswith("+"), "the brief writes the plus sign itself"

    def test_symbol_without_chart_data_has_no_level(self, stocks_client):
        quote = stocks_client.extract_quote_from_chart("^NOPE", {})
        assert quote["price"] is None
        assert quote["change_pct"] is None

    def test_quote_lists_one_row_per_symbol_and_no_price_when_yahoo_is_silent(
        self, stocks_client, monkeypatch, capsys,
    ):
        monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
        monkeypatch.setattr(stocks_client, "yf_chart", lambda *args, **kwargs: None)
        monkeypatch.setattr(stocks_client, "yf_quote_summary", lambda *args, **kwargs: None)

        stocks_client.cmd_quote(["^GSPC", "^IXIC"])
        rows = json.loads(capsys.readouterr().out)
        assert [row["symbol"] for row in rows] == ["^GSPC", "^IXIC"]
        assert all("price" not in row for row in rows)

        stocks_client.cmd_quote(["^GSPC"])
        single = json.loads(capsys.readouterr().out)
        assert isinstance(single, dict) and single["symbol"] == "^GSPC"
