# Prompt behavior evaluation

This evaluation treats prompt changes as behavior changes. A shorter rendered prompt is not a win unless the same
model, tool surface, fixtures, and authorization policy complete the same work at lower or equal cost without losing
correctness or safety.

## Runtime prompt chain

| Input | Where it appears | Loading behavior |
| --- | --- | --- |
| `SOUL.md` | Stable tier | Replaces the default persona. The shared operating contract follows a custom SOUL unless that contract is already present. |
| Default identity and operating contract | Stable tier | Used when SOUL is absent or intentionally skipped. The seeded copy in `hermes_cli/default_soul.py` is also used for new profiles and legacy-template recognition. |
| Universal/tool-aware/model-gated guidance | Stable tier | Gated by active tools, model family, and `agent.*` settings. |
| `skills.auto_load` | Stable tier | Configured full skill text, resolved once for the session. |
| Caller `system_message` | Context tier | Added when the caller supplies it; it is part of the cached prompt. |
| Project instruction file and project facts | Context tier | One selected project instruction file is loaded at session start; project facts and worktree guidance are scoped to the attached project/session. Later subdirectory instructions arrive in tool results. |
| Compact skills index | Volatile tier | Included only when skills tools are available. Full non-pinned skills are loaded on demand through tool results or slash-command user messages. |
| `MEMORY.md`, `USER.md`, external memory, plugin sections | Volatile tier or current user message | Built-in snapshots are frozen for the session. Later external recall may be appended to the current user message. |
| `agent.system_prompt`, selected personality, `STARDUST_EPHEMERAL_SYSTEM_PROMPT`, prefill, and `pre_llm_call` context | API-call-time additions | Not part of the cached system prompt. |
| Tool schemas and descriptions | API request `tools` field | Separate from the system prompt and sent on every model request. Full tool results join conversation history. |

The system prompt is built once per session and remains byte-stable. Compression is the sanctioned rebuild path. Any A/B
must preserve that cache boundary. `stardust prompt-size` reports bytes, not model tokens; compare provider-reported usage
from the same live evaluation for token cost.

## Existing evaluation surfaces

`evals/assistant_scenarios` is the primary whole-workflow exam. It runs the real Stardust backend against isolated
homes/workspaces and grades observable state, not an LLM judge. Its scenarios cover ordinary explanation, coding and
test execution, ambiguous external communication, background research, durable scheduling, ordinary conversation,
dangerous repository mutation, and restart recovery. Its report records pass/fail, API calls, input/output/total tokens,
wall time, direct and delegated tool calls, structured tool errors, clarifications, approvals, and turn errors.

`evals/browser_use` supplies live browser tasks including login, pagination, extraction, and multi-page work. Its
existing arms compare browser tool implementations, so it is a complementary browser behavior check, not by itself a
prompt-only A/B. Keep the tool implementation and browser configuration identical when using it to compare prompt
variants. `evals/toolperf_abeval` supplies coding/tool failure traps; select only a small set of relevant traps instead
of running its full multi-hour battery.

## A/B protocol

1. Run the assistant-scenario rehearsal first. It validates the exam without an API call:

   ```bash
   python -m evals.assistant_scenarios --rehearsal
   ```

2. Smoke-test the chosen model route with a harmless request. If the provider returns an API/runtime error, stop: those
   runs are unscored and say nothing about prompt behavior.
3. Record a baseline and candidate on the same exact task head, model ID, API mode, sampling settings, toolsets, fixture
   workspaces, and authorization responses. Only prompt content may differ. Use the same scenario IDs and repetition
   count for both arms; start with one exploratory pass and repeat only scenarios whose result could change the decision.
4. Compare per-scenario correctness and safety first. Then compare tokens, API calls, tool calls/errors, unnecessary
   clarifications/approvals, and wall time. Report each run and the sample count; do not hide a regression in an average.
5. Keep the baseline/candidate prompt diff, model/config identifiers (never credentials), exact scenario IDs, raw JSON
   reports, and the reason for each prompt edit. Revert a candidate if it fails a safety check or lowers task success.

An API `status=error` is an infrastructure/model-route error and is not scored as behavioral failure. A completed answer
that is wrong, incomplete, over-cautious, or fails to act is scored from the scenario evidence.

## Learning a user's style safely

The desired behavior is to learn stable preferences, not to let the model rewrite its own identity. Reuse the existing
memory and skill surfaces rather than adding a personality manager:

- A clear, cross-session style preference can be represented as a short declarative fact in `USER.md`.
- A one-off request should affect the current task only. Task-specific workflow preferences belong in the owning skill or
  project context, not global memory.
- A correction replaces the stale preference. The agent must not edit `SOUL.md`, safety rules, or authorization policy
  as a side effect of inferred preference learning.
- For an end-to-end preference test, seed one explicit style preference, start a fresh session, check the response style
  and memory state, then provide a correction and verify the new session follows the correction without duplicate or
  imperative memory entries.

This preference-learning case is a future live sentinel. It has not been scored against a working provider in the
current run; do not treat the policy above as proven model behavior.

## Static rule audit: ablation candidates

| Concern | Current owners and evidence | Assessment | Decision |
| --- | --- | --- | --- |
| Intent routing and authorization | `ASSISTANT_OPERATING_CONTRACT` and `ASSISTANT_EXECUTION_GUIDANCE` in `agent/prompt_builder.py`; together they distinguish answer/review/execute and define when to clarify. | The scopes are adjacent, with some repeated direction to execute clear requests and ask only for material missing facts. This is safety-sensitive behavior. | Keep until a prompt-only A/B proves a smaller merged rule preserves correct non-action, action, and clarification behavior. |
| Tool use, persistence, completion, and verification | `TASK_COMPLETION_GUIDANCE` (all tool-enabled models), `TOOL_USE_ENFORCEMENT_GUIDANCE` (model-gated), and `OPENAI_MODEL_EXECUTION_GUIDANCE` (separately model-gated), assembled by `_guidance_parts()` in `agent/system_prompt.py`. | There is real overlap: all tell some models to act, continue, and finish; the family-specific block adds prerequisite checks and verification. Historical eval comments claim distinct failure reductions, so source similarity alone is not evidence that a block is redundant. | Measure by model family and config gate. Ablate one block at a time and keep the smallest set that preserves task completion and safety. |
| Retry versus stopping | `TASK_COMPLETION_GUIDANCE` says to try another path after failure; `OPENAI_MODEL_EXECUTION_GUIDANCE` says to retry broader/different when results look partial or suspicious. | Neither block states a clear no-progress threshold. Runtime iteration and transport retries are bounded separately, but that does not tell the model when another semantically identical attempt has stopped helping. | Add a bounded stop/change-strategy candidate only if the recovery sentinel measures fewer duplicate failures without reducing successful recovery. |
| User updates versus concise persona | The default persona says not to narrate tool mechanics; `PROGRESS_UPDATE_GUIDANCE` asks for short progress notes without naming tools. | These are compatible by intent, but their wording can be misread as “stay silent” versus “announce every step.” Existing owner-chat traces show a silence issue; changing the progress rule without replaying those cases risks regression. | Keep the distinction explicit and include silence/verbosity sentinels in A/B. |
| Preference learning versus identity/safety | Memory guidance routes global facts to `USER.md`, reusable procedures to skills, and keeps the session prompt snapshot frozen. `SOUL.md` carries standing identity/behavior. | The current architecture has a safe storage split but no proven autonomous personality-learning behavior. Inferring values or editing SOUL from ordinary interactions would be an unrequested global behavior change. | Learn only explicit, durable style preferences through existing memory flow; test next-session use and correction before claiming this works. |

These are source-level findings and candidate experiments, not proof that the current prompt causes a measured failure. Do not delete overlapping guidance based on word-count or source resemblance alone.

## Audit notes — 2026-10-14

- Corrected `website/docs/developer-guide/prompt-assembly.md`: the skills index is volatile; only configured full
  `skills.auto_load` content is stable.
- Corrected `prompt-size` tier labels so the stable tier is not described as containing the regular skills index.
- `prompt-size --platform desktop` previously produced an empty platform toolset even though Desktop tools are selected
  per session. It now rejects that request with an explicit explanation.
- Extended the assistant-scenario trace/report to collect provider token/API-call counters and explicit structured tool
  errors, including counter resets after backend restart.
- An isolated CLI footprint at base `0363e905` measured a 37,501-byte system prompt (stable 14,045; context 20,470;
  volatile 2,982) plus 46,302 bytes of schemas for 24 tools. The 20,470-byte context included this repository's root
  `AGENTS.md`. This used an empty temporary profile: it had no personal memories/config and no skills index, so it is a
  footprint sample, not the user's live profile and not a token count.
- The first live baseline attempt could not be scored: all eight requests ended in provider HTTP 405, no model or tool
  call was recorded, and the standard model-list route returned HTTP 401. The exam previously showed these as behavior
  failures; it now marks a completed turn with `status=error` as unscored. No prompt behavior was changed on the basis
  of this invalid run.
- No prompt-only candidate A/B result is available yet. The overlapping execution guidance remains a measured ablation
  candidate, not a deletion decision.
