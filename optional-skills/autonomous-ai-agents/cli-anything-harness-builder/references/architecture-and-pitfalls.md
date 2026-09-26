# Architecture patterns and pitfalls

Read this before writing `core/export.py` or `utils/<software>_backend.py`.

## The backend module

`utils/<software>_backend.py` is the **only** module allowed to shell out to
the real software. Everything else calls into it. It owns:

- **Finding the executable** — `shutil.which("<software>")`, raising a clear
  `RuntimeError` with per-OS install instructions when it's missing (never a
  silent fallback):

  ```python
  def find_libreoffice():
      path = shutil.which("libreoffice") or shutil.which("soffice")
      if path:
          return path
      raise RuntimeError(
          "LibreOffice is not installed. Install it with:\n"
          "  apt install libreoffice   # Debian/Ubuntu\n"
          "  brew install libreoffice  # macOS\n"
          "  winget install TheDocumentFoundation.LibreOffice  # Windows"
      )
  ```

- **Invoking it** — via `subprocess.run`, headless/background flags, capturing
  stdout/stderr for error surfacing.
- **Returning a structured result** — a dict with the output path, format, and
  which method produced it, so the caller (and any `--json` output) can report
  it without re-deriving it.

## The rendering gap (failure mode #2, after reimplementing the software)

Most GUI apps apply effects at render time via their own engine. A CLI that
edits the *project file* directly still has to get those edits rendered — and
a naive renderer will silently ignore them, producing output that looks
identical to the untouched source with no visible error.

**Concretely:** you add a filter to the MLT project XML. If you then render
with a tool that reads raw media and ignores project-level filters (e.g. a
plain ffmpeg concat), the filter is invisible in the output. Nothing crashes;
the result is just wrong.

Priority order for rendering, best to worst:

1. **The app's own native renderer** (`melt` for MLT, `blender --background`
   for `.blend`) — it understands every field in the project format by
   construction.
2. **A translation layer** you write, converting the project format's effects
   into the rendering tool's native syntax (e.g. MLT filters → ffmpeg
   `-filter_complex`). Every effect/filter in your registry needs a mapping
   here, or an explicit "project-only, not rendered" note — don't let one
   silently no-op.
3. **A generated script the user runs manually**, as a last resort when
   neither of the above is feasible.

## Timecode and non-integer frame rates

29.97fps and similar non-integer rates accumulate rounding error fast. If the
harness touches video timing at all: use `round()`, never `int()`, when
converting between frames and seconds; do timeline math in integer frame
counts, not float seconds, wherever you can; and accept ±1 frame as correct
when comparing against an expected value. Fetch
[`guides/timecode-precision.md`](https://github.com/HKUDS/CLI-Anything/blob/main/cli-anything-plugin/guides/timecode-precision.md)
from upstream for the full treatment before shipping a video/audio harness.

## Filter translation

If you're building the translation layer in step 2 above, watch for: two
filters merging into one and losing a parameter, stream ordering that doesn't
match the source project once interleaved, and parameter scales that differ
between the two formats (e.g. 0–1 vs. 0–100). Fetch
[`guides/filter-translation.md`](https://github.com/HKUDS/CLI-Anything/blob/main/cli-anything-plugin/guides/filter-translation.md)
from upstream when this applies.

## When there's no CLI at all: the MCP backend pattern

Some software (browser automation being the common case) exposes an MCP
server instead of a traditional CLI. The backend module then wraps an MCP
client instead of `subprocess`, but the same contract holds: the backend
module is still the only place that talks to the real thing, and the CLI
layer above it looks the same to callers either way. Fetch
[`guides/mcp-backend.md`](https://github.com/HKUDS/CLI-Anything/blob/main/cli-anything-plugin/guides/mcp-backend.md)
from upstream for the session-management and daemon-mode details before
building one of these.

## Session file locking

If the harness persists a session/project file that could be written
concurrently, use exclusive file locking on save: open in `"r+"`, lock, then
truncate-and-write inside the lock — never a bare overwrite. Fetch
[`guides/session-locking.md`](https://github.com/HKUDS/CLI-Anything/blob/main/cli-anything-plugin/guides/session-locking.md)
from upstream for the exact pattern.
