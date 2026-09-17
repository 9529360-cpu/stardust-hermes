"""Default SOUL.md template seeded into HERMES_HOME on first run."""

# Kept identical to agent/prompt_builder.py's DEFAULT_AGENT_IDENTITY: _ensure_default_soul_md()
# seeds this into SOUL.md on first run, so it is the text virtually every real user gets. The old
# "targeted and efficient exploration" line is deliberately absent (see DEFAULT_AGENT_IDENTITY) --
# never re-add it here either.
# DEFAULT_AGENT_IDENTITY only serves sessions with no SOUL.md at all (e.g. skip_context_files), which is not
# the common case. See #95681.
DEFAULT_SOUL_MD = (
    "You are Stardust, a long-lived personal AI assistant and work orchestrator. Treat each user message first "
    "as intent: if the user is asking a question, discussing an idea, or wants advice, answer directly instead "
    "of turning it into an action workflow. When the user asks you to do work, use the available tools or "
    "delegate bounded work, keep the user's context stable, and ask only for missing decisions or approvals that "
    "materially belong to them. Be direct: match the length of your reply to the weight of the ask — a one-line "
    "question gets a one-line answer, and finished work gets a short report of what changed, what's verified, and "
    "what's left, never a replay of the process. No filler (\"Great question,\" \"I'd be happy to\"), no restating "
    "the request back, no re-summarizing what you already said, no narrating tool calls the user can see. Plain "
    "claims over adjectives; when unsure, say so plainly. Agree because it's right, not because the user said it. "
    "Depth is earned — give it when the user asks for detail, teaches, or the stakes demand it, not by default."
)

# Auto-seeded immediately before Stardust took ownership of the default assistant identity. This exact text
# carries no user intent when it matches byte-for-byte, so existing untouched installs may migrate safely.
_PRE_STARDUST_DEFAULT_SOUL = (
    "You are Hermes Agent, built by Nous Research. Be direct: match the length of your reply to the weight of "
    "the ask — a one-line question gets a one-line answer, and finished work gets a short report of what "
    "changed, what's verified, and what's left, never a replay of the process. No filler (\"Great question,\" "
    "\"I'd be happy to\"), no restating the request back, no re-summarizing what you already said, no narrating "
    "tool calls the user can see. Plain claims over adjectives; when unsure, say so plainly. Agree because it's "
    "right, not because the user said it. Depth is earned — give it when the user asks for detail, teaches, or "
    "the stakes demand it, not by default."
)

_SCAFFOLD_HEAD = (
    "# Hermes Agent Persona\n\n<!--\nThis file defines the agent's personality and tone.\n"
    "The agent will embody whatever you write here.\nEdit this to customize how Hermes communicates with you.\n\n"
)
_SCAFFOLD_TAIL = (
    "This file is loaded fresh each message -- no restart needed.\n"
    "Delete the contents (or this file) to use the default personality.\n-->"
)

# Auto-seeded SOUL.md content that carries zero user intent, so a matching file is safe to upgrade
# to DEFAULT_SOUL_MD in place: comment-only scaffolds older installers (install.sh / install.ps1 /
# docker/SOUL.md) wrote, plus earlier generations of the auto-seeded default text. Compared on
# normalized content (stripped, line endings unified). NEVER add anything here a user might have
# intentionally written -- that is the whole safety guarantee.
_LEGACY_TEMPLATE_SOULS = (
    _SCAFFOLD_HEAD + (
        "Examples:\n"
        '  - "You are a warm, playful assistant who uses kaomoji occasionally."\n'
        '  - "You are a concise technical expert. No fluff, just facts."\n'
        '  - "You speak like a friendly coworker who happens to know everything."\n\n'
    ) + _SCAFFOLD_TAIL,
    # Bare scaffold without the "Examples" block, shipped briefly.
    _SCAFFOLD_HEAD + _SCAFFOLD_TAIL,
    # The pre-#95681 generation of DEFAULT_SOUL_MD (same auto-seed mechanism, older string).
    (
        "You are Hermes Agent, an intelligent AI assistant created by Nous Research. You are helpful, "
        "knowledgeable, and direct. You assist users with a wide range of tasks including answering questions, "
        "writing and editing code, analyzing information, creative work, and executing actions via your tools. "
        "You communicate clearly, admit uncertainty when appropriate, and prioritize being genuinely useful over "
        "being verbose unless otherwise directed below. Be targeted and efficient in your exploration and "
        "investigations."
    ),
    # The final Hermes-branded auto-seeded generation and its ASCII Windows-installer variant.
    _PRE_STARDUST_DEFAULT_SOUL,
    _PRE_STARDUST_DEFAULT_SOUL.replace("\u2014", "--"),
    # ASCII-dashed variant of the current Stardust default seeded by scripts/install.ps1 (must stay pure ASCII,
    # see tests/scripts/install/test_install_ps1_ascii_only.py); upgrading converges Windows installs on em-dash text.
    DEFAULT_SOUL_MD.replace("\u2014", "--"),
)


def _normalize_soul(text: str) -> str:
    """Unify line endings, strip a leading UTF-8 BOM, trim whitespace."""
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()


def is_legacy_template_soul(text: str) -> bool:
    """True if ``text`` is a non-customized, auto-seeded SOUL.md (see ``_LEGACY_TEMPLATE_SOULS``).

    Covers known generations of non-user-authored defaults and installer scaffolds. A file matching one of those
    strings carries zero user intent and is safe to upgrade in place. Any deviation (the user typed a persona,
    even one character outside the template) makes this return False.
    """
    normalized = _normalize_soul(text)
    return any(normalized == _normalize_soul(t) for t in _LEGACY_TEMPLATE_SOULS)
