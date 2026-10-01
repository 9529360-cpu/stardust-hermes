#!/usr/bin/env python3
"""Check an .xlsx for structure Excel refuses, optionally repair it, and open it in Excel on Windows.

Prints one JSON object {"ok", "problems", "fixed", "excel"} and exits 0 when ok:

  python scripts/xlsx_verify.py out.xlsx          # report
  python scripts/xlsx_verify.py out.xlsx --fix    # repair what it can, then report
  python scripts/xlsx_verify.py out.xlsx --no-excel   # skip the real-Excel open check

Dependency-free (zipfile + ElementTree), so it runs where openpyxl is not installed. "excel" is
"opened", "refused", "unavailable" (not Windows, or no Excel) or "skipped".
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _bounds(ref: str) -> tuple[int, int, int, int]:
    """'A1:D4' (or 'A1') -> (min_col, min_row, max_col, max_row)."""
    corners = []
    for cell in ref.replace("$", "").split(":"):
        letters, digits = re.match(r"([A-Za-z]*)(\d*)", cell).groups()
        col = 0
        for ch in letters.upper():
            col = col * 26 + ord(ch) - 64
        corners.append((col, int(digits or 0)))
    (c1, r1), (c2, r2) = corners[0], corners[-1]
    return min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)


def _overlap(a: str, b: str) -> bool:
    a1, a2, a3, a4 = _bounds(a)
    b1, b2, b3, b4 = _bounds(b)
    return a1 <= b3 and b1 <= a3 and a2 <= b4 and b2 <= a4


def _rels(book: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relationship id -> package path for *part*."""
    path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if path not in book.namelist():
        return {}
    targets = {}
    for rel in ET.fromstring(book.read(path)).iter(PKG + "Relationship"):
        target = rel.get("Target", "")
        targets[rel.get("Id")] = (target.lstrip("/") if target.startswith("/")
                                  else posixpath.normpath(posixpath.join(posixpath.dirname(part), target)))
    return targets


def find_problems(book: zipfile.ZipFile) -> list[dict]:
    """Sheet-level AutoFilters that overlap a table on the same sheet (Excel refuses the workbook)."""
    rels, found = _rels(book, "xl/workbook.xml"), []
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    for index, sheet in enumerate(workbook.iter(MAIN + "sheet")):
        part = rels.get(sheet.get(REL_ID))
        if part not in book.namelist():
            continue
        root = ET.fromstring(book.read(part))
        sheet_filter = root.find(MAIN + "autoFilter")
        if sheet_filter is None:
            continue
        sheet_rels = _rels(book, part)
        for table_part in root.iter(MAIN + "tablePart"):
            table_path = sheet_rels.get(table_part.get(REL_ID))
            if table_path not in book.namelist():
                continue
            table = ET.fromstring(book.read(table_path))
            if _overlap(sheet_filter.get("ref", "A1"), table.get("ref", "A1")):
                found.append({"index": index, "sheet": sheet.get("name"), "part": part,
                              "filter": sheet_filter.get("ref"), "table": table.get("displayName") or table.get("name"),
                              "table_ref": table.get("ref")})
    return found


def _describe(problem: dict) -> str:
    return (f"sheet '{problem['sheet']}': AutoFilter {problem['filter']} overlaps table '{problem['table']}' "
            f"({problem['table_ref']})")


def _fix(path: str, problems: list[dict]) -> None:
    """Drop each overlapping sheet AutoFilter and its _xlnm._FilterDatabase name; the table keeps its own filter."""
    with zipfile.ZipFile(path) as book:
        edits = {"xl/workbook.xml": book.read("xl/workbook.xml").decode("utf-8")}
        for problem in problems:
            xml = edits.get(problem["part"]) or book.read(problem["part"]).decode("utf-8")
            edits[problem["part"]] = re.sub(r"<autoFilter\b[^>]*?/>|<autoFilter\b.*?</autoFilter>", "", xml,
                                            count=1, flags=re.S)

            def drop(match: re.Match, index: int = problem["index"]) -> str:
                attrs = match.group(1)
                is_filter_db = 'name="_xlnm._FilterDatabase"' in attrs
                return "" if is_filter_db and f'localSheetId="{index}"' in attrs else match.group(0)

            edits["xl/workbook.xml"] = re.sub(r"<definedName\b([^>]*)>.*?</definedName>", drop,
                                              edits["xl/workbook.xml"], flags=re.S)
        edits["xl/workbook.xml"] = re.sub(r"<definedNames>\s*</definedNames>", "", edits["xl/workbook.xml"])
        tmp = path + ".verify-tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for item in book.infolist():
                data = edits[item.filename].encode("utf-8") if item.filename in edits else book.read(item.filename)
                out.writestr(item, data)
    os.replace(tmp, path)


def _excel_check(path: str) -> str:
    """Open the workbook read-only in real Excel (Windows): opened / refused / unavailable."""
    if os.name != "nt":
        return "unavailable"
    script = ("try { $xl = New-Object -ComObject Excel.Application } catch { 'unavailable'; exit }; "
              "$xl.DisplayAlerts = $false; "
              "try { $wb = $xl.Workbooks.Open($env:XLSX_VERIFY_PATH, 0, $true); $wb.Close($false); 'opened' } "
              "catch { 'refused' } finally { $xl.Quit() }")
    env = {**os.environ, "XLSX_VERIFY_PATH": os.path.abspath(path)}  # no path quoting through the shell
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, env=env, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return lines[-1] if lines and lines[-1] in ("opened", "refused", "unavailable") else "unavailable"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check an .xlsx for structure Excel refuses.")
    ap.add_argument("path", help="workbook to check")
    ap.add_argument("--fix", action="store_true", help="repair what can be repaired, in place")
    ap.add_argument("--no-excel", action="store_true", help="skip the real-Excel open check")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    with zipfile.ZipFile(args.path) as book:
        found = find_problems(book)
    fixed = []
    if found and args.fix:
        _fix(args.path, found)
        fixed = [_describe(problem) + ": removed the sheet AutoFilter (the table filters itself)" for problem in found]
        with zipfile.ZipFile(args.path) as book:
            found = find_problems(book)
    problems = [_describe(problem) + ": Excel refuses this workbook; run again with --fix" for problem in found]
    excel = "skipped" if args.no_excel else _excel_check(args.path)
    if excel == "refused" and not problems:
        problems.append("Excel refused to open this workbook: the file is broken (not an Excel installation "
                        "problem); rebuild it before reporting it done")
    report = {"ok": not problems, "problems": problems, "fixed": fixed, "excel": excel}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
