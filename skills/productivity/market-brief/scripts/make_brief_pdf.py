#!/usr/bin/env python3
"""Render a Markdown market brief to PDF with reportlab.

    make_brief_pdf.py --reports-dir        print <home>/workspace/reports (created if missing)
    make_brief_pdf.py BRIEF.md [OUT.pdf]   render BRIEF.md; OUT defaults to
                                           <home>/workspace/reports/YYYY-MM-DD-market-brief.pdf

Markdown support: '#' headings (three levels), paragraphs, '-' or '*' bullets, pipe tables with a
separator row, **bold**, [label](http(s)://...) links and '---' rules. Other text is plain
paragraphs. Warnings go to stderr. The last line on stdout is the PDF path.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Optional, Sequence

# The first candidate that exists and reportlab can embed wins. Entries are (path, subfont index).
# reportlab embeds only TrueType outlines, so CFF-based OpenType collections (many Noto CJK
# builds) are skipped with a reason and the next candidate is tried.
CJK_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    (r"C:\Windows\Fonts\msyh.ttc", 0),  # Windows: Microsoft YaHei
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),  # Debian/Ubuntu fonts-noto-cjk
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),  # Debian/Ubuntu fonts-wqy-zenhei (TrueType)
    ("/System/Library/Fonts/PingFang.ttc", 0),  # macOS
    ("/Library/Fonts/Arial Unicode.ttf", 0),  # macOS
)

CJK_FONT_NAME = "StardustCJK"
HELVETICA = ("Helvetica", "Helvetica-Bold")
MARGIN_MM = 18
INK = "#1F2933"
ACCENT = "#1F4E79"
RULE = "#B8C2CC"
HEADER_FILL = "#E8EEF4"
FOOTER_INK = "#6B7785"
BULLET = "\u2022"

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET = re.compile(r"^[-*+]\s+(.+)$")
_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


@dataclass(frozen=True)
class RenderResult:
    output: Path
    font: str                   # family used for body text
    cjk_font: Optional[Path]    # embedded CJK font, or None when Helvetica is used


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def stardust_home() -> Path:
    # Imported lazily so that an explicit output path never needs the Stardust package.
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def reports_dir() -> Path:
    folder = stardust_home() / "workspace" / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_output(today: Optional[dt.date] = None) -> Path:
    return reports_dir() / f"{(today or dt.date.today()).isoformat()}-market-brief.pdf"


def _font_name(path: Path) -> str:
    # reportlab keeps the first font registered under a name, so each font file gets its own name.
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{CJK_FONT_NAME}-{digest}"


def register_cjk_font(candidates: Optional[Sequence[tuple[str, int]]] = None) -> Optional[Path]:
    """Register the first candidate font that reportlab can embed. Returns its path, or None."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if candidates is None:
        candidates = CJK_FONT_CANDIDATES
    problems: list[str] = []
    for raw_path, subfont in candidates:
        path = Path(raw_path)
        if not path.is_file():
            continue
        name = _font_name(path)
        try:
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=subfont))
        except Exception as exc:  # reportlab raises TTFError for outlines it cannot embed
            problems.append(f"{path.name}: {exc}")
            continue
        pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
        return path
    detail = "; ".join(problems) if problems else "no candidate file exists"
    _warn(
        f"no CJK-capable font could be embedded ({detail}). Falling back to Helvetica: Chinese "
        "text will not render in this PDF. Windows needs Microsoft YaHei (msyh.ttc); Linux needs "
        "fonts-wqy-zenhei or another TrueType CJK font."
    )
    return None


def _split_row(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") else body
    return [cell.strip() for cell in body.split("|")]


def _is_separator(line: str) -> bool:
    return "|" in line and all(_SEPARATOR_CELL.match(cell) for cell in _split_row(line))


def parse_markdown(text: str) -> list[tuple]:
    """Split Markdown into blocks: ('heading', level, text), ('paragraph', lines), ('bullet', text),
    ('table', header, rows) and ('rule',)."""
    lines = text.splitlines()
    blocks: list[tuple] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            blocks.append(("paragraph", pending[:]))
            pending.clear()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            flush()
        elif _RULE.match(stripped):
            flush()
            blocks.append(("rule",))
        elif heading := _HEADING.match(stripped):
            flush()
            blocks.append(("heading", min(len(heading.group(1)), 3), heading.group(2)))
        elif stripped.startswith("|") and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            flush()
            header = _split_row(stripped)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = _split_row(lines[i])
                rows.append((row + [""] * len(header))[: len(header)])
                i += 1
            blocks.append(("table", header, rows))
            continue
        elif bullet := _BULLET.match(stripped):
            flush()
            blocks.append(("bullet", bullet.group(1)))
        else:
            pending.append(stripped)
        i += 1
    flush()
    return blocks


def _link_markup(match: re.Match) -> str:
    href = match.group(2).replace('"', "&quot;")
    return f'<a href="{href}"><font color="{ACCENT}">{match.group(1)}</font></a>'


def _inline(text: str) -> str:
    """Escape one line of Markdown and turn **bold** and [label](http(s) URL) into Paragraph markup."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped = _LINK.sub(_link_markup, escaped)
    return _BOLD.sub(r"<b>\1</b>", escaped)


def _build_styles(family: str, bold: str) -> dict:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    ink = colors.HexColor(INK)
    accent = colors.HexColor(ACCENT)
    body = ParagraphStyle(
        "body", fontName=family, fontSize=10, leading=15, spaceAfter=6, textColor=ink, wordWrap="CJK",
    )
    return {
        "body": body,
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=17, leading=23, spaceAfter=8,
                             textColor=accent, wordWrap="CJK"),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13, leading=18, spaceBefore=10, spaceAfter=5,
                             textColor=accent, wordWrap="CJK"),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=11, leading=15, spaceBefore=8, spaceAfter=4,
                             textColor=ink, wordWrap="CJK"),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=14, bulletIndent=3,
                                 bulletFontName=family, spaceAfter=3),
        "cell": ParagraphStyle("cell", fontName=family, fontSize=8.5, leading=11.5, textColor=ink,
                               wordWrap="CJK"),
        "cell_head": ParagraphStyle("cell_head", fontName=bold, fontSize=8.5, leading=11.5,
                                    textColor=ink, wordWrap="CJK"),
    }


def _table(header: list[str], rows: list[list[str]], styles: dict, width: float):
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph, Table, TableStyle

    cell_style = styles["cell"]
    grid = [header, *rows]
    # Weight each column by its widest rendered cell (CJK glyphs are wider than ASCII), clamped
    # so that no column collapses; the weights are then scaled to the full text width.
    weights = [
        min(200.0, max(24.0, max(
            stringWidth(row[col], cell_style.fontName, cell_style.fontSize) for row in grid
        ) + 8))
        for col in range(len(header))
    ]
    col_widths = [width * weight / sum(weights) for weight in weights]
    cells = [[Paragraph(_inline(text), styles["cell_head"]) for text in header]]
    cells += [[Paragraph(_inline(text), styles["cell"]) for text in row] for row in rows]
    table = Table(cells, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(RULE)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(HEADER_FILL)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _story(blocks: list[tuple], styles: dict, width: float) -> list:
    from reportlab.lib import colors
    from reportlab.platypus import HRFlowable, Paragraph, Spacer

    story: list = []
    for block in blocks:
        kind = block[0]
        if kind == "heading":
            story.append(Paragraph(_inline(block[2]), styles[f"h{block[1]}"]))
        elif kind == "paragraph":
            story.append(Paragraph("<br/>".join(_inline(line) for line in block[1]), styles["body"]))
        elif kind == "bullet":
            story.append(Paragraph(_inline(block[1]), styles["bullet"], bulletText=BULLET))
        elif kind == "rule":
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor(RULE),
                                    spaceBefore=4, spaceAfter=6))
        else:  # table
            story.append(_table(block[1], block[2], styles, width))
            story.append(Spacer(1, 8))
    return story


def _draw_footer(canvas, doc, *, font: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    canvas.saveState()
    canvas.setFont(font, 8)
    canvas.setFillColor(colors.HexColor(FOOTER_INK))
    canvas.drawCentredString(A4[0] / 2, 9 * mm, str(doc.page))
    canvas.restoreState()


def render_pdf(
    markdown: str, output: Path, *, candidates: Optional[Sequence[tuple[str, int]]] = None,
) -> RenderResult:
    """Render Markdown text to a PDF file at ``output`` (parent folders are created)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    cjk_font = register_cjk_font(candidates)
    family, bold = (_font_name(cjk_font),) * 2 if cjk_font else HELVETICA
    blocks = parse_markdown(markdown)
    title = next((b[2] for b in blocks if b[0] == "heading" and b[1] == 1), "Market brief")
    margin = MARGIN_MM * mm
    width = A4[0] - 2 * margin

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output), pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=16 * mm, bottomMargin=16 * mm, title=title.replace("**", ""), author="Stardust",
    )
    footer = partial(_draw_footer, font=family)
    doc.build(_story(blocks, _build_styles(family, bold), width), onFirstPage=footer, onLaterPages=footer)
    return RenderResult(output=output, font=family, cjk_font=cjk_font)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Markdown market brief to PDF.")
    parser.add_argument("brief", nargs="?", help="Markdown file to render")
    parser.add_argument("output", nargs="?", help="PDF path (default: the reports folder)")
    parser.add_argument("--reports-dir", action="store_true",
                        help="print the reports folder (created if missing) and exit")
    args = parser.parse_args(argv)

    if args.reports_dir:
        print(reports_dir())
        return 0
    if args.brief is None:
        parser.error("give a Markdown file, or --reports-dir")
    brief = Path(args.brief)
    if not brief.is_file():
        print(f"error: brief not found: {brief}", file=sys.stderr)
        return 1
    try:
        import reportlab  # noqa: F401
    except ImportError:
        print(
            "error: reportlab is not installed. Re-run the Stardust sync with the pdf extra included "
            "(for example: uv sync --locked --extra all), or run: python -m pip install reportlab==5.0.1",
            file=sys.stderr,
        )
        return 2

    output = Path(args.output) if args.output else default_output()
    result = render_pdf(brief.read_text(encoding="utf-8-sig"), output.resolve())
    print(result.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
