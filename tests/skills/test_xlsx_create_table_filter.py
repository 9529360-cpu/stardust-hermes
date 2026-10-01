"""xlsx_create must not write a sheet AutoFilter over a table: Excel refuses that workbook.

Field checkup 2026-10-01: an invoice summary carried both a table and a sheet AutoFilter on A1:D4;
openpyxl read it back fine, but Excel would not open it (not even in repair mode). The script's own
spec example combined the two as well.
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("openpyxl")

SCRIPT = Path(__file__).parents[2] / "skills" / "productivity" / "xlsx" / "scripts" / "xlsx_create.py"


def _build(tmp_path, sheet):
    spec, out = tmp_path / "spec.json", tmp_path / "out.xlsx"
    spec.write_text(json.dumps({"sheets": [sheet]}, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(SCRIPT), str(spec), str(out)], check=True, capture_output=True)
    return zipfile.ZipFile(out)


def test_a_table_over_the_autofilter_range_keeps_only_the_tables_filter(tmp_path):
    book = _build(tmp_path, {"name": "发票", "rows": [["号码", "金额"], ["1", 2], ["3", 4]],
                             "autofilter": "A1:B3", "tables": [{"name": "Invoices", "range": "A1:B3"}]})

    assert b"<autoFilter" not in book.read("xl/worksheets/sheet1.xml")
    assert b"<autoFilter" in book.read("xl/tables/table1.xml")


def test_an_autofilter_away_from_tables_is_kept(tmp_path):
    book = _build(tmp_path, {"name": "Data", "rows": [["a", "b"], [1, 2]] + [[None, None]] * 3 + [["c", "d"], [3, 4]],
                             "autofilter": "A6:B7", "tables": [{"name": "Top", "range": "A1:B2"}]})

    assert b'<autoFilter ref="A6:B7"' in book.read("xl/worksheets/sheet1.xml")
