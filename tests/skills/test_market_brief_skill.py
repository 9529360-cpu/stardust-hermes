"""Tests for the market-brief skill: SKILL.md contract and scripts/make_brief_pdf.py."""

import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("reportlab")
import reportlab  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "productivity" / "market-brief"
SKILL_PATH = SKILL_DIR / "SKILL.md"
SCRIPT_PATH = SKILL_DIR / "scripts" / "make_brief_pdf.py"

SAMPLE_BRIEF = """# 全球股市日报 · 2026-10-11

数据截至 18:00（北京时间）。状态：收盘 / 盘中。

## 主要指数

| 地区 | 指数 | 最新 | 涨跌幅 | 状态 | 来源 |
|---|---|---|---|---|---|
| 美国 | 标普500 (S&P 500) | 未取到 | 未取到 | 前一交易日收盘 | [Example](https://example.com/a?x=1&y=2) |
| 亚洲 | 日经225 | 41,000 | -0.8% | 收盘 | [Example](https://example.com/b) |

## 主要异动

- **宁德时代 (A股)**: +3.2%，超预期。来源：[财经网](https://example.com/news)

---

## 资料来源

1. [Example](https://example.com/a)
"""


@pytest.fixture
def brief_pdf():
    """Load the script as a fresh module so monkeypatched module constants take effect."""
    name = "market_brief_make_brief_pdf_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


def _frontmatter_and_body():
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert content.startswith("---")
    match = re.search(r"\n---\s*\n", content[3:])
    assert match, "frontmatter must close with ---"
    frontmatter = yaml.safe_load(content[3 : match.start() + 3])
    return frontmatter, content[match.end() + 3 :]


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
            hits = list(REPO_ROOT.glob(f"skills/*/{name}/SKILL.md")) + list(
                REPO_ROOT.glob(f"skills/*/*/{name}/SKILL.md")
            )
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

    def test_skill_runs_the_bundled_script(self):
        _, body = _frontmatter_and_body()
        assert "SKILL_DIR/scripts/make_brief_pdf.py" in body
        assert SCRIPT_PATH.is_file()

    def test_skill_never_invents_numbers_and_requires_sources(self):
        _, body = _frontmatter_and_body()
        assert "未取到" in body
        assert "`web_search`" in body and "`web_extract`" in body
        assert "at least three" in body.lower() or "three or more" in body.lower()

    def test_outline_headings_are_in_fixed_order(self):
        _, body = _frontmatter_and_body()
        outline = ["# 全球股市日报", "## 主要指数", "## 主要异动", "## 宏观要闻", "## 资料来源"]
        positions = [body.index(heading) for heading in outline]
        assert positions == sorted(positions)


class TestRenderPdf:
    def test_chinese_markdown_renders_to_a_pdf_file(self, tmp_path, brief_pdf):
        out = tmp_path / "brief.pdf"
        result = brief_pdf.render_pdf(SAMPLE_BRIEF, out)
        data = out.read_bytes()
        assert result.output == out
        assert data.startswith(b"%PDF")
        assert len(data) > 1000

    def test_truetype_font_is_embedded_when_one_is_available(self, tmp_path, brief_pdf):
        # reportlab ships Vera.ttf, so this exercises the embedding path on every host.
        vera = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        out = tmp_path / "vera.pdf"
        result = brief_pdf.render_pdf(SAMPLE_BRIEF, out, candidates=[(str(vera), 0)])
        assert result.cjk_font == vera
        assert result.font.startswith(brief_pdf.CJK_FONT_NAME)
        assert b"FontFile2" in out.read_bytes()

    @pytest.mark.windows_only
    def test_windows_cjk_font_is_embedded(self, tmp_path, brief_pdf):
        msyh = Path(brief_pdf.CJK_FONT_CANDIDATES[0][0])
        if not msyh.is_file():
            pytest.skip("Microsoft YaHei is not installed on this machine")
        out = tmp_path / "msyh.pdf"
        result = brief_pdf.render_pdf(SAMPLE_BRIEF, out)
        assert result.cjk_font == msyh
        assert b"FontFile2" in out.read_bytes()


class TestFallback:
    def test_missing_cjk_font_falls_back_to_helvetica(self, tmp_path, brief_pdf, capsys):
        out = tmp_path / "fallback.pdf"
        result = brief_pdf.render_pdf(SAMPLE_BRIEF, out, candidates=[(str(tmp_path / "none.ttc"), 0)])
        assert result.font == "Helvetica"
        assert result.cjk_font is None
        assert out.read_bytes().startswith(b"%PDF")
        assert "warning" in capsys.readouterr().err

    def test_unreadable_font_file_is_skipped_with_a_warning(self, tmp_path, brief_pdf, capsys):
        broken = tmp_path / "broken.ttc"
        broken.write_bytes(b"not a font file")
        out = tmp_path / "broken.pdf"
        result = brief_pdf.render_pdf(SAMPLE_BRIEF, out, candidates=[(str(broken), 0)])
        assert result.cjk_font is None
        assert out.read_bytes().startswith(b"%PDF")
        err = capsys.readouterr().err
        assert "warning" in err and "broken.ttc" in err

    def test_cli_still_writes_pdf_without_a_cjk_font(self, tmp_path, brief_pdf, monkeypatch, capsys):
        monkeypatch.setattr(brief_pdf, "CJK_FONT_CANDIDATES", ((str(tmp_path / "none.ttc"), 0),))
        brief = tmp_path / "brief.md"
        brief.write_text(SAMPLE_BRIEF, encoding="utf-8")
        out = tmp_path / "out" / "brief.pdf"
        assert brief_pdf.main([str(brief), str(out)]) == 0
        captured = capsys.readouterr()
        assert captured.out.strip().splitlines()[-1] == str(out.resolve())
        assert out.read_bytes().startswith(b"%PDF")
        assert "warning" in captured.err


class TestCommandLine:
    def test_reports_dir_is_created_under_the_stardust_home(self, tmp_path, brief_pdf, monkeypatch, capsys):
        home = tmp_path / "home"
        monkeypatch.setenv("STARDUST_HOME", str(home))
        assert brief_pdf.main(["--reports-dir"]) == 0
        printed = capsys.readouterr().out.strip().splitlines()[-1]
        assert Path(printed) == home / "workspace" / "reports"
        assert (home / "workspace" / "reports").is_dir()

    def test_default_pdf_name_is_dated_inside_reports(self, tmp_path, brief_pdf, monkeypatch):
        monkeypatch.setenv("STARDUST_HOME", str(tmp_path / "home"))
        path = brief_pdf.default_output(dt.date(2026, 10, 11))
        assert path == tmp_path / "home" / "workspace" / "reports" / "2026-10-11-market-brief.pdf"
        assert path.parent.is_dir()

    def test_missing_brief_is_an_error(self, tmp_path, brief_pdf, capsys):
        assert brief_pdf.main([str(tmp_path / "missing.md")]) == 1
        assert "brief not found" in capsys.readouterr().err


class TestMarkdownParsing:
    def test_blocks_keep_document_order(self, brief_pdf):
        kinds = [block[0] for block in brief_pdf.parse_markdown(SAMPLE_BRIEF)]
        assert kinds == [
            "heading", "paragraph", "heading", "table", "heading", "bullet", "rule", "heading", "paragraph",
        ]

    def test_table_rows_are_padded_to_the_header_width(self, brief_pdf):
        text = "| a | b | c |\n|---|---|---|\n| 1 | 2 |\n| 1 | 2 | 3 | 4 |\n"
        (block,) = brief_pdf.parse_markdown(text)
        _, header, rows = block
        assert header == ["a", "b", "c"]
        assert rows == [["1", "2", ""], ["1", "2", "3"]]

    def test_inline_escapes_markup_and_links_only_http(self, brief_pdf):
        markup = brief_pdf._inline("a <b> & [x](https://e.com/?p=1&q=2) [y](javascript:alert(1)) **bold**")
        assert "&lt;b&gt;" in markup and "&amp;" in markup
        assert '<a href="https://e.com/?p=1&amp;q=2">' in markup
        assert "javascript:" in markup and 'href="javascript' not in markup
        assert "<b>bold</b>" in markup
