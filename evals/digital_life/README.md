# Digital-life evals

## Long-term memory retrieval benchmark (DL-P2.6)

Offline, opt-in measurement of the EXISTING long-term memory (topic summaries
+ per-turn recall) on a fixed, committed case set: 100 synthetic facts in 25
fact-domains, written through the real `memory_tool` write path into an
isolated HOME, then 100 questions (40 direct / 20 paraphrase / 20 distractor /
20 outdated) asked through the real production recall path
(`_memory_turn_start_and_prefetch` on a real AIAgent, fresh session per
question, production recall limits). Prior art and baseline numbers:
`D:\wt\logs\RESULT-dl-p2-6.md`.

    python -B -m evals.digital_life.memory_retrieval \
        --home <isolated-home> --output <evidence>/results.json [--qa] [--provenance]

Zero product behaviour change by default: eval-only invocation, no product flag
set. `--qa` answers through the default configured
model via the local magpie gateway (at most 100 calls; skipped automatically
if the gateway is unreachable). The dump-all-memory control in the output is
cost reporting only, never a proposed solution. QA scoring is strict: an
outdated question counts as correct only when the new value appears AND the
superseded value does not (LongMemEval counts "both mentioned" as correct, so
our QA number is a lower bound by that convention).

`--provenance` (added in DL-P2.7) measures the opt-in product flag
`memory.topic_provenance_enabled`: it writes that flag into the isolated
home's config.yaml and stamps the corpus with bi-temporal provenance
(observed_at on every write; expired valid_until on stale forks), so the
flag-on recall path (recency-aware superseding, provenance labels) runs
end-to-end against the same fixed 100 questions.

Case set is regenerated deterministically by
`evals/digital_life/generate_memory_cases.py` (RNG-free tables, fixed seed
20261024); the committed test asserts byte-identical regeneration.

## Understand-user benchmark (DL-P2.8c)

Offline, opt-in measurement of user-intent understanding on a fixed,
committed case set of 30 synthetic, de-identified cases: 20 adapted 1:1
from the DISCOVERY-8 real-failure evidence (per-row root-cause labels) +
10 constructed counter-cases (every root-cause class >=2, including
"explicit user instruction overrides automatic behaviour" and "clear
instruction must NOT trigger clarification"). Each case scores 0/1 and
passes only if all four dimensions hold: intent restated (all constraint
groups surface, no forbidden additions), first action correct (from the
real tool-call trace), completion claims backed by tool-result evidence
when required, and no needless `clarify` calls. Scoring is deterministic
first (keyword/structure/trace checks); the default configured model via
magpie judges only restatement adequacy, at most 1 judge call per case and
2 agent API calls per case per run.

    python -B -m evals.digital_life.understand_user \
        --home <isolated-home> --output <evidence>/results.json \
        [--ab agent.turn_intent=true] [--offline]

Owner hard rule enforced: sha256 of SOUL.md / AGENTS.md / USER.md in the
eval HOME is recorded after product warm-up and re-checked after the run;
any difference fails the run (the product itself writes a default SOUL.md
into a fresh home on first agent construction — the baseline is taken
after that initialisation, and the guard catches anything the RUN changes).
`--ab FLAG=VALUE` writes the flag into the ISOLATED eval home's
config.yaml only (never the user's), so DL-P2.8d/e can A/B off-vs-on
without editing the harness. `--offline` swaps in a deterministic loopback
fake model (harness-mechanics test, zero model calls). Baseline numbers
and prior art: `D:\wt\logs\RESULT-dl-p2-8c.md`.

## Digital-life recovery kill measurement

This offline evaluation changes no product behavior and adds no product flag. It uses the real AIAgent conversation loop, real SessionDB, real write_file tool, and the existing OpenAI-compatible loopback FakeModel (dynamic port 0). No paid model or credentials are used. Product configuration flags remain at shipped defaults; checkpoint and life-event fields can therefore be null/empty. These are measured absences, not fabricated recovery records.

Run from the repository root with the repository Python environment. Set HOME, USERPROFILE, STARDUST_HOME, HERMES_HOME, TEMP and TMP to the same fresh isolated directory outside the source tree before launching:

    python -B -m evals.digital_life.recovery --home <isolated-home> --output <evidence>/results.json

The default is ten cases, distributed 3/3/2/2 across before_tool, after_effect_before_commit, after_commit, before_final. Each case has its own HOME. Existing case directories are refused to prevent overwriting evidence. A second independent invocation with a new HOME and output path measures repeatability. --runs 1 is a targeted smoke check; it is not a ten-case acceptance result.

Each child checks all six HOME variables before product imports. No variable may resolve to D:/Stardust/home or a descendant. The child starts with a minimal environment, disabled bytecode writes, and redirected AppData/cache/login locations. A Python audit hook rejects filesystem mutations outside its case HOME (except the kernel null device). Real file tools may invoke their normal shell backend: this is an evaluation boundary, not an OS sandbox or proof about arbitrary subprocesses. The deterministic endpoint only offers write_file with fixed paths below HOME. Parent evidence outputs are intentional writes outside HOME, not task side effects. The harness limits artifacts to 200 MiB and requires 2 GiB free space. Startup/tool/restart waits have deadlines; every owned child is reaped in finally.

The four-step task writes a.txt, b.txt, c.txt and then summary.txt. The fake model advances using successful committed tool results in conversation history. It never checks the files to select a recovery action. An effect without a committed result is offered again, exposing duplicate side effects. An evaluation-only wrapper records successful actual writes, including rewrites of identical bytes; production dispatch still performs the operation. Tool callbacks gate before b.txt and after b.txt's result commit. The dispatch wrapper gates immediately after b.txt is written but before its result reaches the executor commit. The endpoint gates before returning the final answer after all four effects. The parent checks barrier PID against its Popen PID and hard-kills only that owned process. On Windows it launches the base Python executable with the current dependency search paths to avoid the venv redirector PID ambiguity.

On restart the same session is loaded into a fresh real AIAgent. The host waits on stdin without calling run_conversation: AIAgent is turn-driven and has no standalone startup scheduler. The harness observes 30 seconds without user input, then sends literal continue to that same process and runs the real conversation loop with persisted history. This measures the AIAgent host boundary, not CLI/gateway startup auto-resume. No synthetic empty turn or implicit continuation is dispatched. The baseline idle result reflects this host contract. Future CLI/gateway recovery work needs its own startup-entry integration coverage.

Completion requires all four files to contain the exact expected bytes and the real turn to return RECOVERY_TASK_COMPLETE. Files already present before final-answer kill alone do not count as a completed task. Both denominators include every case; harness_error/child_error is distinct from a recorded negative recovery result and makes the invocation fail. JSON is checkpointed after each case; Markdown is written alongside it. Per-case records include files, effects/duplicate count, checkpoint, life events, process exit, timing, idle observation and error details. Default-disabled life events are read from the existing life_events.db if present, never enabled just for a better score.

Compare repeat runs by matching run/kill_point and the tuple (verdict, auto.completed, continue.completed, continue.duplicate_side_effects), ignoring PIDs and elapsed times. At least nine of ten must match. A pure evaluation patch has identical product code before/after; there is no flag-on configuration or alternate product behavior to compare. Keep origin/main SHA, candidate SHA, targeted tests and both measured summaries in the delivery report.

Targeted boundary tests:

    bash scripts/run_tests.sh tests/agent/test_task_checkpoint.py -q -p no:cacheprovider
