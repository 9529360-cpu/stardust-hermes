"""Curator's source-vs-umbrella preservation tests and archive recovery."""
from __future__ import annotations

from contextvars import copy_context
from pathlib import Path
from unittest.mock import patch

from tools.skill_consolidation_validation import (
    active_validation, capture, compare, curator_validation_pass,
)


def _skill(root: Path, name: str, title: str = "Browser login and session recovery") -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\n" + f"name: {name}\n" + f"description: {title}\n" + "---\n\n"
        + f"# {name}\n\n" + "## Browser login\n\nCheck user identity.\n\n"
        + "## Session recovery\n\nRecover expired sessions safely.\n",
        encoding="utf-8",
    )
    return folder


def test_same_cases_pass_on_original_and_equivalent_umbrella(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation", "Browser login and session recovery and data collection")
    result = compare(capture(source), capture(target))
    assert result["passed"] is True
    assert result["baseline_passed"] == result["candidate_passed"] == result["total"]


def test_missing_original_capability_refuses_merge(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation", "Browser login only")
    (target / "SKILL.md").write_text(
        "---\nname: web-automation\ndescription: Browser login only\n---\n"
        "## Browser login\n\nComplete login.\n",
        encoding="utf-8",
    )
    outcome = compare(capture(source), capture(target))
    assert outcome["passed"] is False
    assert outcome["candidate_passed"] < outcome["baseline_passed"]


def test_missing_script_or_binary_asset_blocks_archive(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    (source / "scripts").mkdir()
    (source / "scripts" / "verify.py").write_text("print('safe')\n", encoding="utf-8")
    assert not compare(capture(source), capture(target))["passed"]
    (target / "scripts").mkdir()
    (target / "scripts" / "verify.py").write_text("print('safe')\n", encoding="utf-8")
    assert compare(capture(source), capture(target))["passed"]
    (target / "scripts" / "verify.py").write_text("print('changed')\n", encoding="utf-8")
    assert not compare(capture(source), capture(target))["passed"]


def test_dangling_relative_links_fail_even_when_text_matches(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    with (target / "SKILL.md").open("a", encoding="utf-8") as f:
        f.write("\n[Detailed process](references/missing.md)\n")
    result = compare(capture(source), capture(target))
    assert not result["passed"]
    assert result["broken_links"] == 1
    (target / "references").mkdir()
    (target / "references" / "missing.md").write_text("# Details\n", encoding="utf-8")
    assert compare(capture(source), capture(target))["passed"]


def test_rejects_symlinked_package_files(tmp_path):
    source = _skill(tmp_path, "browser-login")
    outside = tmp_path / "secret"
    outside.write_text("private", encoding="utf-8")
    try:
        (source / "exposed").symlink_to(outside)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlink creation unsupported")
    assert "symlink" in capture(source).error


def test_bounded_snapshot_fails_closed_on_oversized_content(tmp_path):
    source = _skill(tmp_path, "browser-login")
    (source / "large.txt").write_bytes(b"x" * 1_048_577)
    assert capture(source).error


def test_pass_carries_snapshot_into_copied_tool_contexts(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    with patch("tools.skill_manager_tool._find_skill", side_effect=lambda n: (
            {"path": source} if n == "browser-login" else {"path": target})):
        with curator_validation_pass([{"name": "browser-login"}]) as session:
            assert active_validation() is session
            assert copy_context().run(active_validation) is session
            outcome = session.assess("browser-login", "web-automation", source, target)
            assert outcome["passed"] is True
    assert active_validation() is None


def test_mutated_source_cannot_be_archived_as_if_original(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    with patch("tools.skill_manager_tool._find_skill", return_value={"path": source}):
        with curator_validation_pass([{"name": "browser-login"}]) as run:
            with (source / "SKILL.md").open("a", encoding="utf-8") as f:
                f.write("\nNew unintended edit\n")
            decision = run.assess("browser-login", "web-automation", source, target)
            assert decision["passed"] is False
            assert "changed" in decision["reason"]
            assert run.summary()["blocked"] == 1


def test_second_source_cannot_invalidate_first_archive(tmp_path):
    one = _skill(tmp_path, "browser-login")
    two = _skill(tmp_path, "email-automation", "Email sending and deliverability recovery")
    merged = _skill(tmp_path, "automation", "Browser login and session recovery")
    with patch("tools.skill_manager_tool._find_skill",
               side_effect=lambda n: {"path": one if n == "browser-login" else two}):
        with curator_validation_pass([{"name": "browser-login"}, {"name": "email-automation"}]) as run:
            assert run.assess("browser-login", "automation", one, merged)["passed"]
            run.archived("browser-login", "automation")
            # Destination covers first source, not the second.
            assert not run.assess("email-automation", "automation", two, merged)["passed"]


def test_later_target_regression_restores_archived_source(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    calls = []
    with patch("tools.skill_manager_tool._find_skill",
               side_effect=lambda n: {"path": source} if n == "browser-login" else {"path": target}), \
         patch("tools.skill_usage.restore_skill", side_effect=lambda n: (calls.append(n) or True, "restored")):
        with curator_validation_pass([{"name": "browser-login"}]) as run:
            assert run.assess("browser-login", "web-automation", source, target)["passed"]
            run.archived("browser-login", "web-automation")
            (target / "SKILL.md").write_text(
                "---\nname: web-automation\ndescription: Browser login only\n---\n"
                "## Browser login\n", encoding="utf-8")
            run.finalize()
            assert calls == ["browser-login"]
            assert run.summary()["restored"] == 1


def test_failure_to_restore_is_explicitly_reported(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    with patch("tools.skill_manager_tool._find_skill", return_value={"path": target}), \
         patch("tools.skill_usage.restore_skill", return_value=(False, "storage offline")):
        with curator_validation_pass([{"name": "browser-login"}]) as run:
            # For this scenario provide the original package directly.
            run.originals["browser-login"] = capture(source)
            run.archived("browser-login", "web-automation")
            target.joinpath("SKILL.md").write_text(
                "---\nname: web-automation\ndescription: unrelated\n---\n",
                encoding="utf-8",
            )
            run.finalize()
            assert run.summary()["restore_failed"] == 1
            assert "storage offline" in run.rollback[0]["reason"]


def test_no_cases_does_not_count_as_success(tmp_path):
    source = tmp_path / "thin"
    target = _skill(tmp_path, "web-automation")
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: thin\ndescription: x\n---\n", encoding="utf-8")
    assert not compare(capture(source), capture(target))["passed"]



def test_copied_script_must_remain_discoverable_in_umbrella(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    for folder in (source, target):
        (folder / "scripts").mkdir()
        (folder / "scripts" / "login.py").write_text("print('safe')\n", encoding="utf-8")
    with (source / "SKILL.md").open("a", encoding="utf-8") as f:
        f.write("\n[Login helper](scripts/login.py)\n")
    assert not compare(capture(source), capture(target))["passed"]
    with (target / "SKILL.md").open("a", encoding="utf-8") as f:
        f.write("\n[Login helper](scripts/login.py)\n")
    assert compare(capture(source), capture(target))["passed"]


def test_losing_a_procedure_step_fails_even_when_headings_survive(tmp_path):
    source = _skill(tmp_path, "browser-login")
    target = _skill(tmp_path, "web-automation")
    with (source / "SKILL.md").open("a", encoding="utf-8") as f:
        f.write("\n- Always verify the account token expiry before login attempts.\n")
    result = compare(capture(source), capture(target))
    assert result["passed"] is False
    with (target / "SKILL.md").open("a", encoding="utf-8") as f:
        f.write("\n- Always verify the account token expiry before login attempts.\n")
    assert compare(capture(source), capture(target))["passed"]
