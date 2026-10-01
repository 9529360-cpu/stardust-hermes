"""xlsx_verify.py names the structure Excel refuses, and --fix repairs it.

Re-exam 2026-10-01: the agent opened its own invoice workbook in Excel as told, got the ambiguous
"不能取得类 Workbooks 的 Open 属性", decided Excel was the problem, and shipped a file Excel refuses
(a sheet AutoFilter over a table). The check is dependency-free so it also runs where neither
Excel nor openpyxl is installed.
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "skills" / "productivity" / "xlsx" / "scripts" / "xlsx_verify.py"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _workbook(path: Path, *, sheet_filter: str | None) -> Path:
    """A minimal two-part workbook: one sheet holding table 'Invoices' on A1:B3."""
    sheet_af = f'<autoFilter ref="{sheet_filter}"/>' if sheet_filter else ""
    defined = (f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
               f"'发票'!$A$1:$B$3</definedName></definedNames>" if sheet_filter else "")
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": (f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
                            f'<sheet name="发票" sheetId="1" r:id="rId1"/></sheets>{defined}</workbook>'),
        "xl/_rels/workbook.xml.rels": (f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                                       'Type="x" Target="worksheets/sheet1.xml"/></Relationships>'),
        "xl/worksheets/sheet1.xml": (f'<worksheet xmlns="{MAIN}" xmlns:r="{REL}"><sheetData/>{sheet_af}'
                                     '<tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>'),
        "xl/worksheets/_rels/sheet1.xml.rels": (f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                                                'Type="x" Target="/xl/tables/table1.xml"/></Relationships>'),
        "xl/tables/table1.xml": (f'<table xmlns="{MAIN}" id="1" name="Invoices" displayName="Invoices" '
                                 'ref="A1:B3"><autoFilter ref="A1:B3"/></table>'),
    }
    with zipfile.ZipFile(path, "w") as book:
        for name, xml in parts.items():
            book.writestr(name, xml)
    return path


def _verify(path: Path, *flags: str):
    proc = subprocess.run([sys.executable, str(SCRIPT), str(path), "--no-excel", *flags],
                          capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, json.loads(proc.stdout)


def test_a_sheet_filter_over_a_table_is_reported(tmp_path):
    code, report = _verify(_workbook(tmp_path / "bad.xlsx", sheet_filter="A1:B3"))

    assert code == 1 and not report["ok"]
    assert "Invoices" in report["problems"][0] and "AutoFilter" in report["problems"][0]


def test_fix_removes_the_sheet_filter_and_its_defined_name(tmp_path):
    path = _workbook(tmp_path / "bad.xlsx", sheet_filter="A1:B3")

    code, report = _verify(path, "--fix")

    assert code == 0 and report["ok"] and report["fixed"]
    book = zipfile.ZipFile(path)
    assert b"<autoFilter" not in book.read("xl/worksheets/sheet1.xml")
    assert b"_xlnm._FilterDatabase" not in book.read("xl/workbook.xml")
    assert b"<autoFilter" in book.read("xl/tables/table1.xml")


def test_a_healthy_workbook_passes(tmp_path):
    code, report = _verify(_workbook(tmp_path / "ok.xlsx", sheet_filter=None))

    assert code == 0 and report["ok"] and report["problems"] == []
