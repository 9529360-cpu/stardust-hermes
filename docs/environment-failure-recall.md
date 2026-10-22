# Scoped environment-failure recall

Opt in per profile with `terminal.environment_failure_recall: true` in
`config.yaml` (default `false`). This advisory currently covers foreground local
Bash terminal calls only. Remote backends, background jobs, executable paths,
compound shell commands and wrappers are skipped rather than guessed.

Simple invocations of `rg`, `grep`, `fd`, `python3`, `python`, `node` and `gh` are
eligible. A fact requires a real exit 127, an anchored command-not-found
shell diagnostic, and an independent missing-resolution probe through the same
terminal environment snapshot. Quoted output alone is not evidence.

Facts expire after 24 hours. Every eligible invocation re-probes resolution and
hashes the actual shell PATH/PATHEXT. A changed environment invalidates previous
facts; a newly available command deletes its negative fact even if PATH is
unchanged. Profile/home, OS, host, backend, shell and working directory scope
prevent facts from being reused across different environments. One unchanged
fact can produce at most one hint, only in a later session, capped at 600
characters in the `environment_hint` result field. Dispatch runs the original
command; hints never rewrite, block, or call a model. Probe failures and corrupt
metadata leave normal execution available.

A successful `grep` invocation can become the verified alternative for a prior
missing `rg` only in the same environment/session within five minutes, with the
same two operands and a simple literal search pattern. This is deliberately a
narrow recovery rule, not general semantic command equivalence. Alternative
availability is rechecked before naming it. There is no new Skill writer or
model tool.

`environment_failures.db` under the profile home stores at most 64 facts, using
fields `scope`, `command`, `observed`, `owner`, `origin`, `alternative`, `hinted`.
Scope/owner/session identities are SHA-256 digests. Command/alternative values
are allowlisted executable names, never command lines. Arguments, paths, PATH
contents and raw output are not stored; a temporary in-memory operand digest
links recovery observations. Delete this derived database to clear recall; turn
the setting off to disable all probes and reads/writes. No user memory files are
changed.
