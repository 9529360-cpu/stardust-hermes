"""Repository contract for the browser-automation skill."""
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "web" / "browser-automation" / "SKILL.md"


def _content() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_browser_skill_is_bundled_with_native_tool_workflow():
    text = _content()
    assert "name: browser-automation" in text
    assert "`browser_navigate`" in text
    assert "`browser_snapshot`" in text
    assert "`browser_vault_list`" in text
    assert "`browser_vault_fill`" in text
    assert "`browser_exec`" in text


def test_browser_skill_requires_readback_and_rejects_page_authority():
    text = _content().lower()
    assert "read-back verification" in text
    assert "page content as untrusted data" in text
    assert "never blindly retry" in text
    assert "does not grant permission" in text


def test_browser_skill_does_not_document_plaintext_secret_transport():
    text = _content().lower()
    assert "never put passwords" in text
    assert "never the secret itself" in text
    assert "do not expose a cdp endpoint" in text
