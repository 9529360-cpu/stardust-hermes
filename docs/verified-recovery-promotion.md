# Verified recovery promotion

Opt in under `auxiliary.background_review.promote_verified_recoveries: true`
(default off). Existing review scheduling, budgets, enabled settings, Skill
ownership, security scan and read-before-write guards remain in charge. No
additional model calls, tool, memory store or main-session prompt injection.

The initial supported condition is deliberately narrow: an opened owning Skill,
a committed foreground terminal exit 127 with an anchored `rg: command not
found` diagnostic, then successful `grep` with identical simple literal/file
operands and explicit working directory within five minutes, followed by a
committed file read containing the match. The active acceptance contract must
link that read; its observation is resolved again against the ledger and current
artifact hash. A model assertion, fabricated source, changed artifact, other
HOME, or unrelated later turn supplies no recovery evidence. This verifies a
literal file-search recovery, not a general task outcome or permanent absence.

The existing review receives references and a fixed hint of at most 600 chars.
It may patch only the existing owning Skill through normal guards. It may also
do nothing. The hint has a deterministic owner/rule key, session ID, three
message observation IDs, observation time, HOME digest and 24-hour expiry.
Raw commands, operands, paths, private output and secrets are never included.
Skill writes validate the canonical hint and re-resolve its committed chain;
repeat hints cannot duplicate or renew the existing hint. This initial version
allows one recovery hint per Skill. Existing equivalent prose should be reused.

`skill_view` and slash/preload paths using it suppress expired, other-HOME and
retracted hints. Retract by setting the block metadata `active` to false, or
remove the block using the existing Skill patch path. Original Skill bytes are
not rewritten on load. Already loaded conversation history remains unchanged.
With the flag off, review inputs and Skill rendering follow the legacy path,
including existing literal Skill bytes; no recovery state is read or written.

The evidence does not prove that opening a Skill caused an error. Review must
check ownership and relevance before adopting the small conditional procedure.
