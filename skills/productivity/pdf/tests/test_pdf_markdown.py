"""Behaviour tests for scripts/pdf_markdown.py (Markdown report to PDF with CJK text).

No network and no personal state: every file lives in pytest's tmp_path. The embedding tests use
the Vera.ttf that ships with reportlab, so they run on every host. Tests that need Chinese glyphs
need a CJK TrueType font on the host and skip with a reason when none is installed.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

pytest.importorskip("reportlab")
import reportlab  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pdf_markdown.py"
VERA = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"

SAMPLE = """# Market brief 2026-10-12

Data as of 18:00.
Status: closed.

## Indices

| Region | Index | Change |
|---|---|---|
| US | S&P 500 | **+0.4%** |
| Asia | Nikkei 225 | -0.8% |

- First bullet with [a source](https://example.com/a?x=1&y=2)
- Second bullet

---

Closing paragraph.
"""

CHINESE = """# 全球股市日报 · 2026-10-12

## 主要指数

| 地区 | 指数 | 涨跌幅 |
|---|---|---|
| 亚太地区 | 日经225 | **-0.8%** |
| 美国 | 标普500 | +0.4% |

- 数据截至 18:00（北京时间）。
- 来源：[示例](https://example.com/cn)
"""


@pytest.fixture(scope="module")
def pdf_markdown():
    spec = importlib.util.spec_from_file_location("pdf_markdown", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name while the class is built
    spec.loader.exec_module(module)
    return module


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


def page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def page_content(pdf: bytes) -> bytes:
    """Decoded page content. reportlab writes ASCII85 wrapped around Flate by default."""
    chunks = []
    for match in re.finditer(rb"(?<!end)stream\r?\n(.*?)endstream", pdf, re.S):
        raw = match.group(1).strip()
        if raw.endswith(b"~>"):
            raw = base64.a85decode(raw[:-2])
        try:
            chunks.append(zlib.decompress(raw))
        except zlib.error:
            chunks.append(raw)  # stored uncompressed
    return b"\n".join(chunks)


def test_help_lists_the_options():
    proc = run_cli("--help")
    assert proc.returncode == 0, proc.stderr
    for option in ("--input", "--output", "--title", "--font"):
        assert option in proc.stdout


def test_markdown_subset_renders_pdf_with_json_result(tmp_path):
    source = tmp_path / "report.md"
    source.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "out" / "report.pdf"

    proc = run_cli("--input", str(source), "--output", str(out), "--font", str(VERA))

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert set(result) == {"output", "pages", "cjk_font", "warnings"}
    assert Path(result["output"]) == out.resolve()
    assert Path(result["cjk_font"]) == VERA
    assert result["warnings"] == []
    assert result["pages"] >= 1
    pdf = out.read_bytes()
    assert pdf.startswith(b"%PDF-")
    assert page_count(pdf) == result["pages"]


def test_truetype_font_is_embedded_in_the_pdf(tmp_path):
    source = tmp_path / "report.md"
    source.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "report.pdf"

    proc = run_cli("--input", str(source), "--output", str(out), "--font", str(VERA))

    assert proc.returncode == 0, proc.stderr
    assert b"/FontFile2" in out.read_bytes()


def test_chinese_report_embeds_a_host_cjk_font(tmp_path):
    source = tmp_path / "cn.md"
    source.write_text(CHINESE, encoding="utf-8")
    out = tmp_path / "cn.pdf"

    proc = run_cli("--input", str(source), "--output", str(out))

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    if result["cjk_font"] is None:
        pytest.skip(
            "no CJK TrueType font on this host (Microsoft YaHei, Noto Sans CJK, WenQuanYi Zen Hei, "
            "PingFang, Hiragino Sans GB or Arial Unicode); Chinese rendering was not exercised"
        )
    assert result["warnings"] == []
    assert b"/FontFile2" in out.read_bytes()


def test_title_option_sets_the_pdf_title(tmp_path, pdf_markdown):
    out = tmp_path / "titled.pdf"
    result = pdf_markdown.render_pdf("Body text.\n", out, title="Quarterly Brief", candidates=())
    assert result.pages == 1
    assert b"/Title (Quarterly Brief)" in out.read_bytes()


def test_short_chinese_labels_are_not_narrower_than_their_text(pdf_markdown):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    cjk, _ = pdf_markdown.register_cjk_font()
    if cjk is None:
        pytest.skip("no CJK TrueType font on this host; the Chinese column width was not measured")
    header = ("地区", "涨跌幅")
    rows = (("亚太地区", "-0.8%"), ("美国", "+0.4%"))

    widths = pdf_markdown.column_widths(
        header, rows, font_name=cjk.family, bold_font_name=cjk.bold, font_size=8.5, total_width=493.2,
    )

    assert sum(widths) == pytest.approx(493.2)
    assert widths[0] >= stringWidth("亚太地区", cjk.family, 8.5)
    assert widths[1] >= stringWidth("涨跌幅", cjk.bold, 8.5)


def test_narrow_columns_keep_their_width_when_the_table_is_too_wide(pdf_markdown):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    header = ("Name", "Notes")
    rows = (("A", "word " * 80),)

    widths = pdf_markdown.column_widths(
        header, rows, font_name="Helvetica", bold_font_name="Helvetica-Bold", font_size=8.5, total_width=200,
    )

    assert sum(widths) == pytest.approx(200)
    assert widths[0] == pytest.approx(stringWidth("Name", "Helvetica-Bold", 8.5) + 8)
    assert widths[1] > widths[0]


def test_blocks_keep_document_order_and_drop_rules(pdf_markdown):
    blocks = pdf_markdown.parse_markdown(SAMPLE)

    kinds = [type(block).__name__ for block in blocks]
    assert kinds == [
        "HeadingBlock", "TextBlock", "HeadingBlock", "TableBlock", "BulletBlock", "BulletBlock", "TextBlock",
    ]
    table = blocks[3]
    assert table.header == ("Region", "Index", "Change")
    assert all(len(row) == len(table.header) for row in table.rows)


def test_inline_markup_escapes_xml_and_keeps_only_bold(pdf_markdown):
    assert pdf_markdown.inline_markup("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    markup = pdf_markdown.inline_markup("<font color='red'>x</font> **bold <i>**")

    assert "<font color='red'>" not in markup
    assert "&lt;font color='red'&gt;x&lt;/font&gt;" in markup
    assert "<b>bold &lt;i&gt;</b>" in markup


def test_xml_special_characters_are_drawn_as_visible_text(tmp_path, pdf_markdown):
    source = "Ratio a < b & c > d for <script>x</script>.\n"
    out = tmp_path / "xml.pdf"

    result = pdf_markdown.render_pdf(source, out, candidates=())

    assert result.pages == 1
    content = page_content(out.read_bytes())
    assert b"a < b & c > d for <script>x</script>." in content


def test_only_http_links_become_links_and_urls_cannot_break_out(pdf_markdown):
    markup = pdf_markdown.inline_markup("[ok](https://example.com/a?x=1&y=2)")
    assert 'href="https://example.com/a?x=1&amp;y=2"' in markup

    hostile = pdf_markdown.inline_markup('[x](https://e.com/"onmouseover=1)')
    assert 'href="https://e.com/&quot;onmouseover=1"' in hostile

    assert pdf_markdown.inline_markup("[x](javascript:alert(1))") == "[x](javascript:alert(1))"


def test_without_a_cjk_font_the_report_still_renders_and_warns(tmp_path, pdf_markdown, capsys, monkeypatch):
    monkeypatch.setattr(pdf_markdown, "CJK_FONT_CANDIDATES", ((str(tmp_path / "missing.ttc"), 0),))
    source = tmp_path / "cn.md"
    source.write_text(CHINESE, encoding="utf-8")
    out = tmp_path / "cn.pdf"

    assert pdf_markdown.main(["--input", str(source), "--output", str(out)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["cjk_font"] is None
    assert result["pages"] >= 1
    assert result["warnings"] and "CJK" in result["warnings"][0]
    assert out.read_bytes().startswith(b"%PDF-")


def test_empty_input_fails_with_a_clear_message(tmp_path):
    source = tmp_path / "empty.md"
    source.write_text("", encoding="utf-8")
    out = tmp_path / "empty.pdf"

    proc = run_cli("--input", str(source), "--output", str(out))

    assert proc.returncode != 0
    assert "empty" in proc.stderr
    assert not out.exists()


def test_unusable_font_is_an_error_not_a_silent_fallback(tmp_path):
    source = tmp_path / "report.md"
    source.write_text(SAMPLE, encoding="utf-8")
    font = tmp_path / "broken.ttf"
    font.write_bytes(b"not a font")
    out = tmp_path / "report.pdf"

    proc = run_cli("--input", str(source), "--output", str(out), "--font", str(font))

    assert proc.returncode == 1
    assert "cannot embed font" in proc.stderr
    assert not out.exists()
