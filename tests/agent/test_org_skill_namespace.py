"""A retired org mirror (``skills/_org/``) stays on disk but is never a skill.

Older installs hold ``_org/<org_id>/`` mirrors (plus ``.active_org``, provenance/baseline sidecars and
``.sync_*`` files) written by the retired upstream Skill Sync. That content belongs to another party's
shared set: it must not reach the prompt, ``skill_view``, the curator or ``skill_manage`` (which could
otherwise edit or delete it), and nothing may delete or rewrite it. Every case runs against a per-test
STARDUST_HOME through the real public entry points, with the old ``.active_org`` marker present.
"""

import json
import sys

import pytest

from agent import prompt_builder as pb
from agent import skill_utils as sku
from hermes_constants import get_skills_dir
from tools import skill_manager_tool as smt
from tools import skill_usage as su
from tools.skills_tool import skill_view, skills_list

ORG = sku.ORG_MIRROR_DIR_NAME


def _write_skill(root, rel, name=None, body="body text\n"):
    skill_dir = root.joinpath(*rel.split("/"))
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_name = name or skill_dir.name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: desc of {skill_name}\n---\n{body}", encoding="utf-8")
    return skill_dir


def _retired_files(skills):
    """Every retired-sync file under the skills dir, byte for byte."""
    return {
        path.relative_to(skills).as_posix(): path.read_bytes()
        for path in skills.rglob("*")
        if path.is_file() and (ORG in path.relative_to(skills).parts or path.name.startswith(".sync_"))
    }


@pytest.fixture
def legacy_install():
    skills = get_skills_dir()
    _write_skill(skills, "personal-a")
    _write_skill(skills, f"{ORG}/org-1/shared-x", body="ORG SECRET BODY\n")
    (skills / ORG / ".active_org").write_text("org-1", encoding="utf-8")
    (skills / ORG / "org-1" / ".org-provenance.json").write_text(
        json.dumps({"author_device": "bens-laptop"}), encoding="utf-8")
    (skills / ORG / "org-1" / ".org-baseline.json").write_text("{}", encoding="utf-8")
    (skills / ".sync_state").write_text('{"head": "abc"}', encoding="utf-8")
    (skills / ".sync_device_id").write_text("old-device", encoding="utf-8")
    pb.clear_skills_system_prompt_cache(clear_snapshot=True)
    yield skills
    pb.clear_skills_system_prompt_cache(clear_snapshot=True)


def test_discovery_never_yields_the_mirror_even_with_the_old_marker(legacy_install):
    found = [p.parent.name for p in sku.iter_skill_index_files(legacy_install, "SKILL.md")]
    assert found == ["personal-a"]


def test_is_org_mirror_path_is_relative_to_the_skills_root(legacy_install, tmp_path):
    assert sku.is_org_mirror_path(legacy_install / ORG / "org-1" / "shared-x", legacy_install)
    assert not sku.is_org_mirror_path(legacy_install / "personal-a", legacy_install)
    # A directory merely NAMED like the mirror elsewhere (or above the skills root) is not the mirror.
    assert not sku.is_org_mirror_path(tmp_path / ORG / "skills" / "x", tmp_path / ORG / "skills")


def test_prompt_index_excludes_the_mirror_and_has_no_org_labels(legacy_install):
    out = pb.build_skills_system_prompt()
    assert "personal-a" in out
    assert "shared-x" not in out
    assert "org-shared" not in out and "org:org-1" not in out


def test_stale_snapshot_holding_org_entries_is_rebuilt(legacy_install):
    pb.build_skills_system_prompt()
    snapshot_path = pb._skills_prompt_snapshot_path()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["version"] = pb._SKILLS_SNAPSHOT_VERSION - 1
    snapshot["skills"].append({
        "skill_name": "shared-x", "category": "general", "frontmatter_name": "shared-x",
        "description": "d", "platforms": [], "conditions": {}, "org_id": "org-1", "org_author": "x"})
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    pb.clear_skills_system_prompt_cache()
    assert "shared-x" not in pb.build_skills_system_prompt()


def test_snapshot_manifest_ignores_the_mirror_and_the_marker(legacy_install):
    manifest = {key.replace("\\", "/"): value for key, value in pb._build_skills_manifest(legacy_install).items()}
    assert "personal-a/SKILL.md" in manifest
    assert not [key for key in manifest if key.startswith(ORG)]


@pytest.mark.parametrize("name", ["shared-x", f"{ORG}/org-1/shared-x", "org-1/shared-x"])
def test_skill_view_cannot_load_the_mirror(legacy_install, name):
    result = json.loads(skill_view(name))
    assert result["success"] is False
    assert "ORG SECRET BODY" not in json.dumps(result)
    assert json.loads(skill_view("personal-a"))["success"] is True


def test_skills_list_excludes_the_mirror(legacy_install):
    listing = skills_list()
    assert "personal-a" in listing and "shared-x" not in listing


@pytest.mark.parametrize("action,extra", [
    ("patch", {"old_string": "ORG SECRET", "new_string": "edited"}),
    ("edit", {"content": "---\nname: shared-x\ndescription: d\n---\nrewritten\n"}),
    ("write_file", {"file_path": "references/n.md", "file_content": "x"}),
    ("remove_file", {"file_path": "references/n.md"}),
    ("delete", {}),
])
def test_skill_manage_cannot_modify_or_delete_the_mirror(legacy_install, action, extra):
    before = _retired_files(legacy_install)
    result = json.loads(smt.skill_manage(action=action, name="shared-x", **extra))
    assert result["success"] is False
    assert _retired_files(legacy_install) == before
    assert (legacy_install / ORG / "org-1" / "shared-x" / "SKILL.md").is_file()


def test_editing_a_local_skill_loads_no_upstream_client(legacy_install):
    result = json.loads(smt.skill_manage(
        action="patch", name="personal-a", old_string="body text", new_string="better"))
    assert result["success"] is True
    assert "org_sharing" not in result
    assert not [name for name in sys.modules if name.startswith("tools.skills_sync_client")]


def test_curator_inventory_ignores_the_mirror(legacy_install):
    su.save_usage({"shared-x": {"created_by": "agent"}})
    assert su._find_skill_dir("shared-x") is None
    assert "shared-x" not in su.list_agent_created_skill_names()


def test_retired_sync_flag_has_no_interface_but_old_records_survive(legacy_install):
    assert not hasattr(su, "set_sync") and not hasattr(su, "is_sync_enabled")
    su.save_usage({"personal-a": {"created_by": "agent", "sync": True}})
    assert su.set_pinned("personal-a", True) is True
    assert su.load_usage()["personal-a"]["sync"] is True


def test_every_operation_leaves_the_retired_files_untouched(legacy_install):
    before = _retired_files(legacy_install)
    pb.build_skills_system_prompt()
    skills_list()
    skill_view("shared-x")
    smt.skill_manage(action="delete", name="shared-x")
    smt.skill_manage(action="patch", name="personal-a", old_string="body text", new_string="better")
    assert _retired_files(legacy_install) == before
    assert before  # the fixture really wrote history
