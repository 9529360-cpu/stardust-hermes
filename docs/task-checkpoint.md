Task checkpoint (opt-in)

Set agent.task_checkpoint: true in config.yaml to preserve a bounded task snapshot
in the existing session model_config and compression todo handoff. It defaults to
off; no checkpoint keys, context or handoff changes are produced while disabled.

Version 1 is capped at 4096 UTF-8 bytes. It references the durable todo revision,
status counts, active todo IDs, active acceptance contract/criterion IDs, recent
committed verification/observation IDs, blockers and the next unblocked todo.
Short criterion/next-step text is bounded; raw tool output is never copied.
Completed todo status does not prove acceptance and references do not upgrade
observation tiers. Full source state remains owned by the existing stores.

On current-user continue/resume, a matching session/task/HOME/profile snapshot is
shown as reference with its UTC snapshot time. It is not fresh authorization and
does not dispatch work. An unrelated request suspends the old snapshot before
compression; a bare continuation does not reactivate it. The new goal requires a
newer todo revision before it can replace the old snapshot. Changed revisions,
foreign task/session owners and foreign HOME/profile scopes are not applied and
receive an explicit reason. A compression child receives state only through the
existing explicit parent-to-child publication path, never a cross-session search.
