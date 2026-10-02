---
name: xlsx
description: Create, read, edit Excel .xlsx workbooks and CSVs.
version: 1.3.0
author: Nous Research
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [excel, spreadsheet, xlsx, csv, openpyxl, productivity]
    category: productivity
    related_skills: [docx, pdf, powerpoint]
---

# Xlsx Skill

Work with Excel .xlsx workbooks using Python and openpyxl: build styled
multi-sheet workbooks with formulas and charts, inspect or dump existing
files, edit cells and structure, and convert to/from CSV. All helper
scripts are argparse CLIs that print JSON and use explicit UTF-8 I/O.

## When to Use

- Creating .xlsx reports: multiple sheets, number formats, styling,
  merged cells, freeze panes, autofilter, conditional formatting,
  charts, data-validation dropdowns, native Excel tables, defined
  names, hyperlinks, cell notes, sheet protection.
- Reading a workbook: sheet inventory, dumping data as JSON or CSV,
  listing formulas vs cached values, notes, defined names, tables.
- Editing existing files: set cells, append rows, insert/delete
  rows/columns (reference-aware via `xlsx_restructure.py`),
  copy/rename sheets, tables, names, notes, protection.
- Recalculating formulas headlessly via LibreOffice
  (`xlsx_recalc.py`).
- CSV interop with type inference and non-UTF-8 encodings.
- Not for the legacy .xls binary format (use LibreOffice to convert
  first: `soffice --headless --convert-to xlsx old.xls`).

## Prerequisites

- Python 3.10+ with `openpyxl` (`pip install openpyxl`). No other
  third-party packages are needed; everything else is stdlib.
- Optional: LibreOffice (`soffice`) for headless recalculation or
  format conversion.

## How to Run

Run the helper scripts with the `terminal` tool from this skill's
`scripts/` directory (every script supports `--help`):

```bash
python scripts/xlsx_create.py spec.json report.xlsx   # build from JSON spec
python scripts/xlsx_read.py report.xlsx --sheets      # inventory
python scripts/xlsx_read.py report.xlsx --json --sheet Data
python scripts/xlsx_read.py report.xlsx --formulas
python scripts/xlsx_edit.py report.xlsx --sheet Data --set B2=42 --recalc
python scripts/xlsx_restructure.py report.xlsx --sheet Data --insert-rows 3:2
python scripts/xlsx_recalc.py report.xlsx
python scripts/csv_to_xlsx.py data.csv out.xlsx --encoding utf-8
python scripts/xlsx_to_csv.py report.xlsx out.csv --sheet Data
python scripts/xlsx_verify.py report.xlsx --fix      # before delivering
```

Author the JSON spec with `write_file`, inspect script JSON output with
`read_file` or directly from stdout.

## Quick Reference

| Task | Command |
|---|---|
| Create workbook from spec | `xlsx_create.py spec.json out.xlsx` |
| Check before delivering (Excel will open it) | `xlsx_verify.py f.xlsx [--fix]` |
| Sheet names + dimensions | `xlsx_read.py f.xlsx --sheets` |
| Dump sheet as JSON | `xlsx_read.py f.xlsx --json --sheet S` |
| Dump sheet as CSV | `xlsx_read.py f.xlsx --csv --out d.csv` |
| List formulas + cached values | `xlsx_read.py f.xlsx --formulas` |
| Set a cell / formula | `xlsx_edit.py f.xlsx --set "A1==SUM(B:B)"` |
| Append a row | `xlsx_edit.py f.xlsx --append '[1,"x",true]'` |
| Insert 2 rows, refs NOT shifted | `xlsx_edit.py f.xlsx --insert-rows 3:2` |
| Insert 2 rows, refs shifted | `xlsx_restructure.py f.xlsx --insert-rows 3:2` |
| Delete a column, refs shifted | `xlsx_restructure.py f.xlsx --delete-cols B` |
| Create a native table | `xlsx_edit.py f.xlsx --add-table Sales:A1:C9` |
| Append inside a table | `--table-append 'Sales=["West",5]'` |
| List tables | `xlsx_edit.py f.xlsx --list-tables` |
| Defined names | `--define-name "Rates='Data'!$B$2:$B$9"` / `--delete-name Rates` / `xlsx_read.py f.xlsx --names` |
| Hyperlink | `--hyperlink "A1=https://example.com|Docs"` |
| Cell note | `--note "B2=Check this|Reviewer"`; read via `xlsx_read.py f.xlsx --notes` |
| Protect sheet (see Pitfalls) | `--protect your-password --unlock B2:B9` |
| Recalculate via LibreOffice | `xlsx_recalc.py f.xlsx` |
| Copy / rename sheet | `--copy-sheet Src:New --rename-sheet Old:New` |
| Force recalc on open | `xlsx_edit.py f.xlsx --recalc` |
| CSV -> styled xlsx | `csv_to_xlsx.py in.csv out.xlsx` |
| xlsx -> CSV | `xlsx_to_csv.py f.xlsx out.csv --encoding utf-8` |

## Procedure

1. **Create**: write a JSON spec (schema documented in
   `xlsx_create.py --help` and its docstring). Each sheet supports
   `rows` (scalars or styled cell objects), sparse `cells` overrides,
   `column_widths`, `row_heights`, `merges`, `freeze_panes`,
   `autofilter`, `conditional_formats` (cell_is rules and color
   scales), `charts` (bar/line/pie from cell ranges),
   `validations` (list dropdowns), `tables` (native Excel tables with
   a style name), and `protection`. Workbook-level `defined_names`
   maps names to refs. Cell objects also take `hyperlink` and `note`.
   Typed values: JSON numbers/bools
   pass through; dates use `{"value": "2026-01-31", "type": "date"}`.
   Number formats are Excel format strings: currency `"$#,##0.00"`,
   percent `"0.0%"`, date `"yyyy-mm-dd"`.
2. **Formulas**: set with `"formula": "SUM(B2:B9)"` in the spec or
   `--set "C1==SUM(A:A)"` in the editor. When writing formulas, add
   `"full_calc_on_load": true` (spec) or `--recalc` (editor); this sets
   the workbook's `fullCalcOnLoad` flag so Excel/LibreOffice recompute
   everything on open. openpyxl itself NEVER evaluates formulas.
3. **Read**: `--sheets` for inventory (names, dimensions, merged
   ranges, chart count, tables, protection, defined names),
   `--json`/`--csv` for data, `--formulas` to
   pair each formula string with its cached result, `--notes` for
   cell comments, `--names` for defined names. Cached results
   exist only if the file was last saved by a real spreadsheet app;
   files fresh from openpyxl return `null` there. To materialize
   results headlessly run `xlsx_recalc.py file.xlsx` (uses
   LibreOffice; prints `{"recalculated": false, ...}` and exits 0
   when `soffice` is absent), then reload with `--data-only`.
4. **Edit**: `xlsx_edit.py` applies renames/copies first, then
   structural row/column changes, then `--set`/`--append`. It edits in
   place unless `--out` is given — copy the file first if you need the
   original.
5. **Restructure**: for insert/delete on sheets that have formulas,
   merges, tables, or filters, use `xlsx_restructure.py` instead of
   `xlsx_edit.py`. It rewrites formula references on ALL sheets
   (absolute `$` refs, ranges, cross-sheet refs), shifts merges,
   autofilter, freeze panes, validation and conditional-format
   ranges, table refs, defined names, and row/column dimensions, then
   prints a JSON report including a `not_shifted` list. Rules and
   limits: `references/restructuring.md`.
6. **CSV interop**: `csv_to_xlsx.py` infers int/float/bool/ISO-date
   per cell and styles the header row; `xlsx_to_csv.py` writes ISO
   dates and blank strings for empty cells. Both default to UTF-8 and
   accept `--encoding` (e.g. `utf-8-sig` for Excel-friendly BOM,
   `cp1252` for legacy Windows exports).

## Converting to PDF

LibreOffice converts headlessly (also works for CSV export of a single
sheet):

```bash
soffice --headless --convert-to pdf report.xlsx --outdir out/
soffice --headless --convert-to csv report.xlsx --outdir out/  # 1st sheet only
```

Only the first sheet lands in a CSV; for other sheets use
`xlsx_to_csv.py --sheet NAME`.

On Windows, Microsoft Excel is usually installed but not on PATH:
check for it with `New-Object -ComObject Excel.Application` (or the
`App Paths\excel.exe` registry key), not `Get-Command excel`. When it is
there, Excel's own export matches what the user sees in Excel:

```bash
powershell.exe -NoProfile -NonInteractive -Command '$xl = New-Object -ComObject Excel.Application; $xl.DisplayAlerts = $false; $wb = $xl.Workbooks.Open("C:\work\report.xlsx", 0, $true); $wb.ExportAsFixedFormat(0, "C:\work\report.pdf"); $wb.Close($false); $xl.Quit()'
```

With neither Excel nor `soffice`, install LibreOffice yourself (e.g.
`winget install --exact --id TheDocumentFoundation.LibreOffice`) or
render the PDF from the data with a library, and say which route you
took.

## Pitfalls

- **openpyxl does not calculate.** Formula results are available only
  via `load_workbook(path, data_only=True)` and only when the file was
  previously saved by Excel/LibreOffice. Otherwise you get `None`.
- **`xlsx_edit.py` insert/delete does not shift references** (raw
  openpyxl behavior). Use `xlsx_restructure.py`, which does — but even
  it cannot move chart anchors, images, or conditional-format RULE
  formulas; read its JSON report's `not_shifted` list and
  `references/restructuring.md`.
- **Sheet protection is NOT security.** `--protect` sets the standard
  xlsx sheet-protection hash: it signals "don't edit this" to
  well-behaved apps and nothing more. Anyone can strip it by editing
  the zip's XML or unchecking it in LibreOffice. Never rely on it for
  confidentiality or integrity; it does not encrypt anything.
- **`data_only=True` then save** silently discards all formulas
  (cached values replace them). Never save a workbook loaded that way
  unless that is the goal.
- **Loading strips charts/images**: openpyxl does not round-trip
  charts, so editing a charted workbook and saving drops the charts.
  Re-add charts after editing, or avoid re-saving charted files.
- **CSV locale traps**: always pass explicit encodings (the scripts
  already do) and remember European CSVs often use `;` delimiters and
  decimal commas — use `--delimiter ';'` and expect strings like
  `"12,5"` to stay strings.
- **Dates are datetimes**: Excel stores dates as serial numbers;
  openpyxl returns `datetime`/`date` objects. Dumps here emit ISO
  strings.
- Sheet names are capped at 31 chars and reject `[ ] : * ? / \`.
- **A table and a sheet AutoFilter on the same cells break the file
  in Excel.** A native table filters itself; also setting
  `ws.auto_filter.ref` over it makes Excel refuse the workbook, even in
  repair mode, while openpyxl reads it back fine. Use one or the other
  (`xlsx_create.py` drops the sheet filter where a table overlaps it;
  `xlsx_verify.py --fix` repairs an existing file).

## Verification

- **Before reporting any workbook done, run `xlsx_verify.py out.xlsx`**
  (add `--fix` to repair what it reports). It names structure Excel
  refuses, such as a sheet AutoFilter over a table, and on Windows with
  Excel installed it opens the file in Excel. `"excel": "refused"` means
  the file is broken, not the Excel installation: fix it and check
  again. Never report a workbook Excel refuses as done.
- After creating: `xlsx_read.py out.xlsx --sheets` and confirm sheet
  names, dimensions, merged ranges, and chart counts match intent.
- Dump data with `--json` and compare against the source values.
- After edits: re-dump the touched range; if formulas were written,
  confirm `--formulas` lists them and that `--recalc` was applied.
- After `xlsx_restructure.py`: read its JSON report, then re-run
  `--formulas` and `--sheets` to confirm references and ranges landed
  where expected.
- For a full visual check, open in LibreOffice:
  `soffice --headless --convert-to pdf out.xlsx` and inspect the PDF.
  Reading a file back with openpyxl does not prove Excel accepts it.
