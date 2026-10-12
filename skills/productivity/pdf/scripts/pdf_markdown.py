#!/usr/bin/env python3
"""Render a Markdown report to PDF with embedded CJK text (reportlab platypus).

    pdf_markdown.py --input report.md --output report.pdf [--title TEXT] [--font PATH]

Markdown subset: '#', '##' and '###' headings (deeper levels render as level 3), paragraphs
separated by blank lines (their lines stay line breaks), '-', '*' or '+' bullets, pipe tables with
a separator row, **bold**, [label](http(s)://...) links (rendered as the label followed by the URL
in small text) and horizontal rules (not drawn). Every literal character is XML-escaped before it
reaches reportlab markup, so text from the web cannot inject markup.

Fonts: --font embeds the given TrueType file. Without it, the first candidate that exists and that
reportlab can embed is used: Windows Microsoft YaHei, then Linux Noto Sans CJK or WenQuanYi Zen
Hei, then macOS PingFang, Hiragino Sans GB or Arial Unicode. Bold uses the bold face next to the
regular one when that exists (Microsoft YaHei Bold on Windows). With no CJK font the report still
renders in Helvetica; the result then has "cjk_font": null and a warning.

Prints a JSON result on stdout: {"output", "pages", "cjk_font", "warnings"}.
Exit status: 0 on success, 1 for unusable input or font, 2 when reportlab is missing.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

# Tried in order; the first file that exists and that reportlab can embed wins. Entries are
# (path, subfont index). reportlab embeds TrueType outlines only, so CFF-based OpenType collections
# (many Noto CJK builds) are skipped with a recorded reason and the next candidate is tried.
CJK_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    (r"C:\Windows\Fonts\msyh.ttc", 0),  # Windows: Microsoft YaHei
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),  # Debian/Ubuntu fonts-noto-cjk (CFF)
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),  # Debian/Ubuntu fonts-wqy-zenhei (TrueType)
    ("/System/Library/Fonts/PingFang.ttc", 0),  # macOS
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),  # macOS
    ("/Library/Fonts/Arial Unicode.ttf", 0),  # macOS
)
# Bold faces that ship beside a regular CJK face. Used only when both files exist; otherwise bold
# text uses the regular face.
CJK_BOLD_FACES: dict[str, tuple[str, int]] = {
    r"C:\Windows\Fonts\msyh.ttc": (r"C:\Windows\Fonts\msyhbd.ttc", 0),
}

FAMILY_PREFIX = "StardustCJK"
HELVETICA = ("Helvetica", "Helvetica-Bold")
MARGIN_MM = 18
INK = "#1F2933"
ACCENT = "#1F4E79"
RULE = "#B8C2CC"
HEADER_FILL = "#E8EEF4"
FOOTER_INK = "#6B7785"
BULLET = "\u2022"
CELL_FONT_SIZE = 8.5
CELL_PADDING = 4  # points, each side

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET = re.compile(r"^[-*+]\s+(.+)$")
_RULE = re.compile(r"^([-*_])(?:\s*\1){2,}$")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_INLINE = re.compile(r"\*\*(?P<bold>.+?)\*\*|\[(?P<label>[^\]]+)\]\((?P<url>https?://[^\s)]+)\)")


@dataclass(frozen=True)
class HeadingBlock:
    level: int
    text: str


@dataclass(frozen=True)
class TextBlock:
    lines: tuple[str, ...]


@dataclass(frozen=True)
class BulletBlock:
    text: str


@dataclass(frozen=True)
class TableBlock:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


Block = Union[HeadingBlock, TextBlock, BulletBlock, TableBlock]


@dataclass(frozen=True)
class EmbeddedFont:
    path: Path
    family: str  # regular face, as registered with reportlab
    bold: str    # bold face; equals ``family`` when no bold face exists


@dataclass(frozen=True)
class RenderResult:
    output: Path
    pages: int
    cjk_font: Optional[Path]
    warnings: tuple[str, ...]


class RenderError(Exception):
    """Input or font that cannot produce a PDF. The message is meant for the user."""


def _reconfigure_stdio() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# --- Markdown parsing -------------------------------------------------------------------------


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _UNESCAPED_PIPE.split(body)]


def _is_separator(line: str) -> bool:
    return "|" in line and all(_SEPARATOR_CELL.match(cell) for cell in _split_row(line))


def _fit(cells: list[str], width: int) -> tuple[str, ...]:
    # GitHub-style tables: short rows are padded with empty cells, extra cells are dropped.
    return tuple((cells + [""] * width)[:width])


def parse_markdown(text: str) -> list[Block]:
    """Split Markdown into blocks, in document order."""
    lines = text.splitlines()
    blocks: list[Block] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            blocks.append(TextBlock(tuple(pending)))
            pending.clear()

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if not line or _RULE.match(line):
            flush()
            index += 1
        elif heading:
            flush()
            blocks.append(HeadingBlock(min(len(heading.group(1)), 3), heading.group(2).strip()))
            index += 1
        elif "|" in line and index + 1 < len(lines) and _is_separator(lines[index + 1]):
            flush()
            header = _split_row(line)
            rows: list[tuple[str, ...]] = []
            index += 2
            while index < len(lines) and "|" in lines[index]:
                rows.append(_fit(_split_row(lines[index]), len(header)))
                index += 1
            blocks.append(TableBlock(_fit(header, len(header)), tuple(rows)))
        elif bullet:
            flush()
            blocks.append(BulletBlock(bullet.group(1)))
            index += 1
        else:
            pending.append(line)
            index += 1
    flush()
    return blocks


# --- Inline markup ----------------------------------------------------------------------------


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _link_markup(label: str, url: str) -> str:
    href = html.escape(url, quote=True)
    return (
        f'<a href="{href}"><font color="{ACCENT}">{_escape(label)}</font></a>'
        f' <font size="7.5" color="{FOOTER_INK}">{_escape(url)}</font>'
    )


def inline_markup(text: str, *, inside_bold: bool = False) -> str:
    """Paragraph markup for one Markdown line. Literal text is XML-escaped; only **bold** and
    http(s) links become markup, and each literal piece is escaped on its own."""
    parts: list[str] = []
    position = 0
    for match in _INLINE.finditer(text):
        parts.append(_escape(text[position:match.start()]))
        if match.group("bold") is not None:
            if inside_bold:
                parts.append(_escape(match.group(0)))  # nested ** stays literal
            else:
                parts.append(f"<b>{inline_markup(match.group('bold'), inside_bold=True)}</b>")
        else:
            parts.append(_link_markup(match.group("label"), match.group("url")))
        position = match.end()
    parts.append(_escape(text[position:]))
    return "".join(parts)


def _plain(text: str) -> str:
    """Visible text of one Markdown line: bold markers and link URLs dropped, labels kept."""
    return _INLINE.sub(
        lambda m: _plain(m.group("bold")) if m.group("bold") is not None else m.group("label"),
        text,
    )


# --- Fonts ------------------------------------------------------------------------------------


def _font_name(path: Path, *, bold: bool = False) -> str:
    # reportlab keeps the first font registered under a name, so each font file gets its own name.
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{FAMILY_PREFIX}{'Bold' if bold else ''}-{digest}"


def _embed(path: Path, subfont: int) -> EmbeddedFont:
    """Register a TrueType face (and its bold partner, when present) and map the family onto them.
    Raises if reportlab cannot embed the file."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    family = _font_name(path)
    pdfmetrics.registerFont(TTFont(family, str(path), subfontIndex=subfont))
    bold = family
    partner = CJK_BOLD_FACES.get(str(path))
    if partner is not None and Path(partner[0]).is_file():
        bold = _font_name(Path(partner[0]), bold=True)
        pdfmetrics.registerFont(TTFont(bold, partner[0], subfontIndex=partner[1]))
    pdfmetrics.registerFontFamily(family, normal=family, bold=bold, italic=family, boldItalic=bold)
    return EmbeddedFont(path=path, family=family, bold=bold)


def register_cjk_font(
    candidates: Optional[Sequence[tuple[str, int]]] = None,
) -> tuple[Optional[EmbeddedFont], list[str]]:
    """Embed the first candidate that exists and that reportlab can embed.

    Returns the font, or None when no candidate works, plus the reason each skipped file failed.
    """
    if candidates is None:
        candidates = CJK_FONT_CANDIDATES
    skipped: list[str] = []
    for raw_path, subfont in candidates:
        path = Path(raw_path)
        if not path.is_file():
            continue
        try:
            return _embed(path, subfont), skipped
        except Exception as exc:  # reportlab raises TTFError for outlines it cannot embed (e.g. CFF)
            skipped.append(f"{path.name}: {exc}")
    return None, skipped


def _embed_explicit(path: Path) -> EmbeddedFont:
    try:
        return _embed(path, 0)
    except Exception as exc:
        raise RenderError(f"cannot embed font {path}: {exc}") from exc


def _no_cjk_warning(skipped: list[str]) -> str:
    detail = "; ".join(skipped) if skipped else "none of the candidate font files exists"
    return (
        f"no CJK-capable TrueType font could be embedded ({detail}), so Chinese text will not "
        "render. Install Microsoft YaHei (Windows) or Noto Sans CJK / WenQuanYi Zen Hei (Linux), "
        "or pass --font PATH."
    )


# --- Layout -----------------------------------------------------------------------------------


def column_widths(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    font_name: str,
    bold_font_name: str,
    font_size: float,
    total_width: float,
    min_width: float = 24.0,
    padding: float = 2 * CELL_PADDING,
) -> list[float]:
    """Column widths in points that sum to ``total_width``.

    A column's natural width is its widest visible text (the header in the bold face) plus
    padding, and never less than ``min_width``. When everything fits, the spare width is shared in
    proportion to the natural widths, so no column is narrower than its longest text. When it does
    not fit, a common level caps the wide columns and narrow columns keep their natural width, so
    short labels such as a region or a percentage are not wrapped.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    natural: list[float] = []
    for col, title in enumerate(header):
        texts = [(_plain(title), bold_font_name)] + [(_plain(row[col]), font_name) for row in rows]
        widest = max(stringWidth(text, name, font_size) for text, name in texts)
        natural.append(max(min_width, widest + padding))

    total_natural = sum(natural)
    if total_natural <= total_width:
        scale = total_width / total_natural
        return [width * scale for width in natural]

    low, high = 0.0, max(natural)
    for _ in range(60):  # bisection on the common cap; sum(min(w, cap)) grows with cap
        cap = (low + high) / 2
        if sum(min(width, cap) for width in natural) > total_width:
            high = cap
        else:
            low = cap
    return [min(width, low) for width in natural]


def _styles(family: str, bold: str) -> dict:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    ink = colors.HexColor(INK)
    accent = colors.HexColor(ACCENT)
    body = ParagraphStyle(
        "body", fontName=family, fontSize=10, leading=15, spaceAfter=6, textColor=ink, wordWrap="CJK",
    )
    cell = ParagraphStyle(
        "cell", fontName=family, fontSize=CELL_FONT_SIZE, leading=11.5, textColor=ink, wordWrap="CJK",
    )
    return {
        "body": body,
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=17, leading=23, spaceAfter=8,
                             textColor=accent, wordWrap="CJK", keepWithNext=1),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13, leading=18, spaceBefore=10, spaceAfter=5,
                             textColor=accent, wordWrap="CJK", keepWithNext=1),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=11, leading=15, spaceBefore=8, spaceAfter=4,
                             textColor=ink, wordWrap="CJK", keepWithNext=1),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=14, bulletIndent=3,
                                 bulletFontName=family, spaceAfter=3),
        "cell": cell,
        "cell_head": ParagraphStyle("cell_head", parent=cell, fontName=bold),
    }


def _table(block: TableBlock, styles: dict, width: float):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    cell = styles["cell"]
    widths = column_widths(
        block.header, block.rows,
        font_name=cell.fontName, bold_font_name=styles["cell_head"].fontName,
        font_size=cell.fontSize, total_width=width,
    )
    data = [[Paragraph(inline_markup(text), styles["cell_head"]) for text in block.header]]
    data += [[Paragraph(inline_markup(text), cell) for text in row] for row in block.rows]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(RULE)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(HEADER_FILL)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), CELL_PADDING),
        ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PADDING),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _story(blocks: list[Block], styles: dict, width: float) -> list:
    from reportlab.platypus import Paragraph, Spacer

    story: list = []
    for block in blocks:
        if isinstance(block, HeadingBlock):
            story.append(Paragraph(inline_markup(block.text), styles[f"h{block.level}"]))
        elif isinstance(block, TextBlock):
            story.append(Paragraph("<br/>".join(inline_markup(line) for line in block.lines), styles["body"]))
        elif isinstance(block, BulletBlock):
            story.append(Paragraph(inline_markup(block.text), styles["bullet"], bulletText=BULLET))
        else:
            story.append(_table(block, styles, width))
            story.append(Spacer(1, 8))
    return story


def _draw_footer(canvas, doc, *, family: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    canvas.saveState()
    canvas.setFont(family, 8)
    canvas.setFillColor(colors.HexColor(FOOTER_INK))
    canvas.drawCentredString(A4[0] / 2, 9 * mm, str(doc.page))
    canvas.restoreState()


# --- Rendering --------------------------------------------------------------------------------


def render_pdf(
    markdown: str,
    output: Path,
    *,
    title: Optional[str] = None,
    font: Optional[Path] = None,
    candidates: Optional[Sequence[tuple[str, int]]] = None,
) -> RenderResult:
    """Render Markdown text to a PDF at ``output`` (parent folders are created).

    ``font`` embeds that TrueType file instead of auto-detecting one. ``candidates`` overrides
    CJK_FONT_CANDIDATES for the auto-detection.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    blocks = parse_markdown(markdown)
    if not blocks:
        raise RenderError("the Markdown input is empty or has no renderable content")

    warnings: list[str] = []
    if font is not None:
        cjk = _embed_explicit(font)
    else:
        cjk, skipped = register_cjk_font(candidates)
        if cjk is None:
            warnings.append(_no_cjk_warning(skipped))
    family, bold = (cjk.family, cjk.bold) if cjk else HELVETICA

    doc_title = title or next(
        (_plain(block.text) for block in blocks if isinstance(block, HeadingBlock) and block.level == 1),
        output.stem,
    )
    margin = MARGIN_MM * mm
    width = A4[0] - 2 * margin
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output), pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin, title=doc_title,
    )

    pages = 0

    def footer(canvas, doc_template) -> None:
        nonlocal pages
        pages = doc_template.page
        _draw_footer(canvas, doc_template, family=family)

    doc.build(_story(blocks, _styles(family, bold), width), onFirstPage=footer, onLaterPages=footer)
    return RenderResult(output=output, pages=pages, cjk_font=cjk.path if cjk else None, warnings=tuple(warnings))


# --- CLI --------------------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    _reconfigure_stdio()
    parser = argparse.ArgumentParser(description="Render a Markdown report to PDF with Chinese text.")
    parser.add_argument("--input", required=True, type=Path, help="Markdown file (UTF-8)")
    parser.add_argument("--output", required=True, type=Path, help="PDF file to write")
    parser.add_argument("--title", help="PDF title metadata (default: the first '# ' heading, else the file name)")
    parser.add_argument("--font", type=Path, help="TrueType font to embed instead of auto-detection")
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 1
    if args.font is not None and not args.font.is_file():
        print(f"error: font not found: {args.font}", file=sys.stderr)
        return 1
    try:
        import reportlab  # noqa: F401
    except ImportError:
        print(
            "error: reportlab is not installed. Install it with: python -m pip install reportlab==5.0.1",
            file=sys.stderr,
        )
        return 2

    try:
        markdown = args.input.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        print(f"error: input is not UTF-8 text: {args.input}", file=sys.stderr)
        return 1
    try:
        result = render_pdf(markdown, args.output.resolve(), title=args.title, font=args.font)
    except RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "output": str(result.output),
        "pages": result.pages,
        "cjk_font": str(result.cjk_font) if result.cjk_font else None,
        "warnings": list(result.warnings),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
