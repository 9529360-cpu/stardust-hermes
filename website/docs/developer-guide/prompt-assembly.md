---
sidebar_position: 5
title: "Prompt Assembly"
description: "How Stardust builds the system prompt, preserves cache stability, and injects session context"
---

# Prompt Assembly

Stardust deliberately separates:

- **cached system prompt state**
- **ephemeral API-call-time additions**

This is one of the most important design choices in the project because it affects:

- token usage
- prompt caching effectiveness
- session continuity
- memory correctness

Primary files:

- `run_agent.py`
- `agent/prompt_builder.py`
- `tools/memory_tool.py`

## Cached system prompt layers

The cached system prompt is assembled as three ordered tiers (see `agent/system_prompt.py`):

1. **stable** — `SOUL.md` or the default Stardust identity and operating contract; universal, tool-aware, and model-gated guidance; configured auto-loaded skills; and the coding brief
2. **context** — caller-supplied `system_message`; the selected project instruction file; project facts; then the worktree snapshot and remaining workspace/platform guidance
3. **volatile** — compact skills index; built-in `MEMORY.md` and `USER.md` snapshots; external memory and plugin sections; timestamp/session/model/provider line; runtime environment hints

The final system prompt is then joined as: `stable` → `context` → `volatile`.

This ordering matters for precedence discussions:
- the compact skills index is in the **volatile** tier; full content for configured `skills.auto_load` entries is in **stable**
- memory/profile snapshots are part of the **volatile** tier
- both are still in the cached system prompt (they are not injected as ad-hoc mid-turn overlays)

Tool schemas and their descriptions are sent in the API request's `tools` field, separately from the system prompt.
They are included on every model request. The compact skills index is present only when skills tools are available;
full skill files are normally returned as tool results when `skill_view` is called. Skill slash commands inject the
selected content as a user message. Neither path silently changes the already-cached system prompt.

Inside the context tier the shared project files come **before** anything naming the current worktree.
Sessions of one project running in different git worktrees then share a prompt prefix covering the whole
context block, instead of stopping at the first cwd-dependent line — that prefix is what a longest-prefix
provider cache reuses. A session with no workspace snapshot keeps its trailing guidance in the stable
tier; the runtime environment block always ends the volatile tier.

Consequence for stored prompts: `_stored_prompt_matches_runtime()` (`agent/conversation_loop.py`) reads
the first host-info paragraph after the rendered `# Hermes runtime environment` boundary, with a
closing marker at the absolute end distinguishing this layout from legacy prose quoting the heading.
The runtime boundary follows all project, operator, memory and plugin text, so examples in those
blocks do not masquerade as the runtime cwd. Model/provider are read before the runtime boundary,
excluding embedder descriptions. `Platform:` is deliberately not an identity field: a surface switch
(desktop ↔ TUI) keeps the stored bytes and delivers the current surface's guidance as a one-shot note on
the per-turn user-message channel (`agent/surface_switch.py`), so the cached prefix survives (#104414). Legacy prompts retain their original
host-before-context anchor, so prompts persisted before the reorder still validate.

When `skip_context_files` is set and the caller has not separately requested SOUL loading (for example, some subagent
sessions), the hardcoded `DEFAULT_AGENT_IDENTITY` is used instead.

### Schematic: assembled system prompt

This schematic shows the order. Actual sections are gated by the active tools, model, profile, project, and configuration:

```
## Stable
[SOUL.md, or Stardust's default identity plus the operating contract]
[Tool-aware guidance and any model-gated guidance]
[Configured auto-loaded skills and coding brief]

## Context
[Caller system_message, selected project instruction file, project facts]
[Workspace snapshot and platform guidance when this session has a project]

## Volatile
[Compact skills index, memory/profile snapshots, external memory/plugin sections]
[Session timestamp and runtime environment]

## Separate API request fields
[Tool schemas and descriptions; full tool results and conversation history]
```

## Customizing platform hints

The platform hint is per-surface guidance Stardust
adds for Telegram, WhatsApp, Slack, CLI, and other platforms — for
example "you are on a terminal, avoid Markdown." The built-in defaults
live in `PLATFORM_HINTS` (`agent/system_prompt.py`); plugin-provided
platforms supply theirs through the platform registry.

An administrator can append to or replace a single platform's hint from
`config.yaml` via the top-level `platform_hints` key, without touching
any other platform:

```yaml
platform_hints:
  whatsapp:
    append: >
      When tabular output would be useful, invoke the table_formatting
      skill instead of emitting a Markdown table.
  slack:
    replace: "You are on Slack. Keep responses tight and avoid wide tables."
  telegram: "Prefer short messages; split long answers."   # shorthand = append
```

- `append` — keep the built-in hint and add the extra text after it.
- `replace` — substitute the built-in hint entirely.
- A bare string — shorthand for `append`.
- `replace` wins over `append` when both are present.
- A malformed entry is ignored defensively and falls back to the
  unmodified default, so a bad config value can never break prompt
  assembly or leak across platforms.

The override is resolved when the system prompt is built (session start,
and again on compaction since that rebuilds the prompt). It produces a
byte-stable hint for a fixed config, so it lives in the **stable** tier
alongside the built-in hint and does not break prompt caching — it is
not a live mid-session mutation of a frozen prompt.

## How SOUL.md appears in the prompt

`SOUL.md` lives at `~/.hermes/SOUL.md` and serves as the agent's identity — the very first section of the system prompt. The loading logic in `prompt_builder.py` works as follows:

```python
# From agent/prompt_builder.py (simplified)
def load_soul_md() -> Optional[str]:
    soul_path = get_hermes_home() / "SOUL.md"
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    content = _scan_context_content(content, "SOUL.md")  # Security scan
    content = _truncate_content(content, "SOUL.md")       # Cap scales with model context window (20k floor); config override wins
    return content
```

When `load_soul_md()` returns content, it replaces the hardcoded `DEFAULT_AGENT_IDENTITY`. A custom file replaces the persona only: unless it already contains `ASSISTANT_OPERATING_CONTRACT` verbatim (the seeded default does), `SOUL_OPERATING_DEFAULTS` follows it — that contract under an `# Operating defaults` heading, with explicit persona instructions taking precedence. The `build_context_files_prompt()` function is then called with `skip_soul=True` to prevent SOUL.md from appearing twice (once as identity, once as a context file).

If `SOUL.md` is absent or intentionally skipped, the system uses `DEFAULT_AGENT_IDENTITY` from
`agent/prompt_builder.py`. That fallback identifies the assistant as Stardust and includes the operating contract.
The text is maintained in code; the profile seeding copy in `hermes_cli/default_soul.py` is kept aligned with it.

## How context files are injected

`build_context_files_prompt()` uses a **priority system** — only one project context type is loaded (first match wins):

```python
# From agent/prompt_builder.py (simplified)
def build_context_files_prompt(cwd=None, skip_soul=False):
    cwd_path = Path(cwd).resolve()

    # Priority: first match wins — only ONE project context loaded
    project_context = (
        _load_hermes_md(cwd_path)       # 1. .hermes.md / HERMES.md (walks to git root)
        or _load_agents_md(cwd_path)    # 2. AGENTS.md (cwd only)
        or _load_claude_md(cwd_path)    # 3. CLAUDE.md (cwd only)
        or _load_cursorrules(cwd_path)  # 4. .cursorrules / .cursor/rules/*.mdc
    )

    sections = []
    if project_context:
        sections.append(project_context)

    # SOUL.md from HERMES_HOME (independent of project context)
    if not skip_soul:
        soul_content = load_soul_md()
        if soul_content:
            sections.append(soul_content)

    if not sections:
        return ""

    return (
        "# Project Context\n\n"
        "The following project context files have been loaded "
        "and should be followed:\n\n"
        + "\n".join(sections)
    )
```

### Context file discovery details

| Priority | Files | Search scope | Notes |
|----------|-------|-------------|-------|
| 1 | `.hermes.md`, `HERMES.md` | CWD up to git root | Stardust/Hermes-compatible project config |
| 2 | `AGENTS.md` | CWD only | Common agent instruction file |
| 3 | `CLAUDE.md` | CWD only | Claude Code compatibility |
| 4 | `.cursorrules`, `.cursor/rules/*.mdc` | CWD only | Cursor compatibility |

All context files are:
- **Security scanned** — checked for prompt injection patterns (invisible unicode, "ignore previous instructions", credential exfiltration attempts)
- **Truncated** — capped at `context_file_max_chars` characters using a 70/20 head/tail split with a truncation marker. The cap scales with the model's context window (20,000-char floor, 500K ceiling); an explicit `context_file_max_chars` in `config.yaml` always wins.
- **YAML frontmatter stripped** — `.hermes.md` frontmatter is removed (reserved for future config overrides)

## API-call-time-only layers

These are intentionally *not* persisted as part of the cached system prompt:

- `ephemeral_system_prompt`
- `agent.system_prompt`, the selected personality, or `STARDUST_EPHEMERAL_SYSTEM_PROMPT` when they resolve to that overlay
- prefill messages
- gateway-derived session context overlays
- later-turn Honcho/external recall injected into the current-turn user message

`pre_llm_call` plugin context also lands in this API-call-time path: it is appended to the current turn's **user message**, not written into the cached system prompt. When multiple plugins return context, Stardust concatenates those context blocks (see [Hooks → `pre_llm_call`](../user-guide/features/hooks.md#pre_llm_call)).

This separation keeps the stable prefix stable for caching.

## Memory snapshots

Local memory and user profile data are captured in the system prompt's **volatile tier**. Mid-session writes update disk state but do not mutate the already-built cached system prompt until a rebuild path runs (new session, or explicit invalidation/rebuild flow such as compression-triggered rebuild).

## Context files

`agent/prompt_builder.py` scans and sanitizes project context files using a **priority system** — only one type is loaded (first match wins):

1. `.hermes.md` / `HERMES.md` (walks to git root)
2. `AGENTS.md` (CWD at startup; subdirectories discovered progressively during the session via `agent/subdirectory_hints.py`)
3. `CLAUDE.md` (CWD only)
4. `.cursorrules` / `.cursor/rules/*.mdc` (CWD only)

`SOUL.md` is loaded separately via `load_soul_md()` for the identity slot. When it loads successfully, `build_context_files_prompt(skip_soul=True)` prevents it from appearing twice.

Long files are truncated before injection.

## Skills index

When skills tools are available, the compact catalog is in the volatile system-prompt tier. Configured
`skills.auto_load` skills contribute their full text to the stable tier. Other full skill text is loaded on demand
through `skill_view` (a tool result) or a skill slash command (a user message). The model does not receive every
installed `SKILL.md` on every request.

## Tool descriptions

Tool schemas, including their descriptions and parameter definitions, are passed in the API request's `tools` field,
not embedded in the system prompt. The active toolset is resolved for the session and its schemas are sent on every
model request. `stardust prompt-size` reports schema bytes separately from system-prompt bytes; those byte counts are
not token counts.

## Supported prompt customization surfaces

Most users should treat `agent/prompt_builder.py` as implementation code, not a configuration surface. The supported customization path is to change the prompt inputs Stardust already loads, rather than editing Python templates in place.

### Use these surfaces first

- The active Stardust profile's `SOUL.md` — customize persona and standing behavior. The shared operating contract follows a custom SOUL unless that file already contains it.
- The active profile's `memories/MEMORY.md` and `memories/USER.md` — provide durable facts and user-profile data that are snapshotted into new sessions. Use Stardust's profile-aware home resolution; the inherited `HERMES_HOME` name is a compatibility interface.
- Project context files such as `.hermes.md`, `HERMES.md`, `AGENTS.md`, `CLAUDE.md`, or `.cursorrules` — inject repo-specific working rules.
- Skills — package reusable workflows and references without editing core prompt code.
- Caller-supplied `system_message` — add context to the cached prompt when a surface explicitly uses this parameter.
- `agent.system_prompt` or the selected `display.personality` — provide a session overlay; these resolve to `ephemeral_system_prompt`, not the cached `system_message` tier.
- `STARDUST_EPHEMERAL_SYSTEM_PROMPT` or prefill messages — add API-call-time guidance that should not become part of the cached prompt prefix.

### When to edit code instead

Edit `agent/prompt_builder.py` only when intentionally changing Stardust's global behavior. That file assembles prompt content, cache boundaries, and injection order for every session. Direct edits there are product changes, not per-user prompt customization.

In other words:

- if you want a different assistant identity, edit `SOUL.md`
- if you want different repo rules, edit project context files
- if you want reusable operating procedures, add or modify skills
- if you want to change how Stardust assembles prompts for everyone, change Python and treat it as a code contribution

## Why prompt assembly is split this way

The architecture is intentionally optimized to:

- preserve provider-side prompt caching
- avoid mutating history unnecessarily
- keep memory semantics understandable
- let gateway/ACP/CLI add context without poisoning persistent prompt state

## Related docs

- [Context Compression & Prompt Caching](./context-compression-and-caching.md)
- [Session Storage](./session-storage.md)
- [Gateway Internals](./gateway-internals.md)
