"""The release generator must not direct new Stardust releases to upstream."""

import importlib.util
from pathlib import Path


RELEASE = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def test_changelog_uses_stardust_links_and_title():
    spec = importlib.util.spec_from_file_location("stardust_release", RELEASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    commit = {
        "category": "fixes",
        "subject": "fix: recover a task (#321)",
        "github_author": "@contributor",
        "sha": "abcdef123456",
        "short_sha": "abcdef1",
    }
    notes = module.generate_changelog([commit], "v2026.10.6", "1.2.3", first_release=True)
    assert notes.startswith("# Stardust v1.2.3")
    assert "https://github.com/9529360-cpu/stardust-hermes/pull/321" in notes
    assert "for Hermes Agent" not in notes


def test_release_publication_copy_is_stardust_owned():
    # Publication requires credentials and must never execute in a unit test.
    # Pin only the generated tag/release title and manual recovery copy.
    source = RELEASE.read_text(encoding="utf-8")
    assert 'f"Stardust v{new_version} ({calver_date})\\n\\nStardust release"' in source
    assert '"--title", f"Stardust v{new_version} ({calver_date})"' in source
    assert "gh release create {tag_name} --title 'Stardust v{new_version}" in source
    assert "https://github.com/NousResearch/hermes-agent" not in source
