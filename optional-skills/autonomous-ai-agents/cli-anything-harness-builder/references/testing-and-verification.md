# Testing and verification

Read this before writing `test_full_e2e.py`. Four layers, each catching a
different class of bug — a harness that only has layer 1 has not been tested
in any way that matters to whether it actually works.

## The four layers

1. **Unit tests** (`test_core.py`) — synthetic data, no external dependencies,
   every function tested in isolation. Fast, deterministic, fine for CI. This
   layer alone proves nothing about whether the real software cooperates.
2. **E2E — intermediate files** (`test_full_e2e.py`) — verify the project
   files your CLI generates are structurally valid (real XML, a real ZIP for
   OOXML/ODF formats, etc.) *before* handing them to the real software.
3. **E2E — true backend** (`test_full_e2e.py`) — **must invoke the real
   software.** Create a project, export via the actual backend, verify the
   real output file. **No graceful degradation**: if the software isn't
   installed, the test fails — it does not skip. A skip here is how a broken
   backend integration ships unnoticed.
4. **CLI subprocess tests** — invoke the *installed* `cli-anything-<software>`
   command via `subprocess`, not a source import, so you're testing what a
   user or agent actually runs:

   ```python
   def _resolve_cli(name):
       """Resolve the installed command; falls back to `python -m` for dev.
       Set CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command."""
       import shutil, sys, os
       path = shutil.which(name)
       if path:
           return [path]
       if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED") == "1":
           raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
       module = name.replace("cli-anything-", "cli_anything.") + "." + name.split("-")[-1] + "_cli"
       return [sys.executable, "-m", module]
   ```

   Never hardcode `sys.executable` or a module path directly — always go
   through `_resolve_cli`. Don't set `cwd` on these calls: an installed
   command has to work from any directory. Run with
   `CLI_ANYTHING_FORCE_INSTALLED=1 pytest ... -v -s` before calling the
   harness done — the `-s` flag shows which binary actually ran.

## "It exited 0" is not verification

Every export/render function needs its output checked programmatically, not
assumed correct because the process didn't crash:

- **Magic bytes** — `%PDF-` for PDF, etc.
- **ZIP structure** — DOCX/XLSX/PPTX/ODF are all ZIP containers; open them as
  one and check the expected internal parts exist.
- **Pixel-level checks** for images/video — probe specific frames (first
  frame near-black for a fade-in, middle frames for a color-grade effect vs.
  the source), and when comparing across resolutions, exclude
  letterboxing/pillarboxing black bars from the comparison.
- **Audio** — RMS levels at the start/end for a fade, spectral comparison
  against the source for an effect.
- **Print the artifact path** in every E2E test (`print(f"PDF: {path}
  ({size:,} bytes)")`) so a human can open and eyeball it — the test passing
  is not the same as the output being *good*.

## TEST.md: plan, then results

Write `tests/TEST.md` **before** any test code, as a real plan: which modules
get unit tests and roughly how many, what E2E workflows will be simulated
(name each one, e.g. "podcast production," "YouTube-style cut/trim," and what
gets verified for it), and what realistic multi-step scenarios matter for this
specific software (heavy undo/redo, save/load round-trips of a complex
project, iterative add/modify/remove/re-add). After the suite passes, append
the real `pytest -v --tb=no` output and a short coverage-gaps note — the file
ends up documenting both the plan and the result in one place.
