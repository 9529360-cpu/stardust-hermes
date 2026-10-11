/** Longest first-message excerpt a session falls back to, counted in characters. */
export const SESSION_TITLE_EXCERPT_CHARS = 40

/** The bot plugin's registry title (`CANONICAL_CHAT_TITLE` in
 *  plugins/hermes-bots/canonical-chat.ts). Restated here because the core
 *  sidebar must not import a plugin module. */
export const CANONICAL_BOT_CHAT_TITLE = 'Bot Chat'

/**
 * Is this row a bot's canonical Bot Chat? The backend decides it the same way
 * (hermes_state_titles.py): the registry title, compared exactly, on a HIDDEN
 * session. A visible session that merely has the name is an ordinary chat, and
 * a hidden side-chat keeps its own name, so neither is canonical.
 */
export function isCanonicalBotChatSession(session: { hidden?: boolean | number | null; title?: null | string }): boolean {
  return Boolean(session.hidden) && session.title === CANONICAL_BOT_CHAT_TITLE
}

/**
 * Titles that name plumbing, not a conversation: the bot plugin's registry row
 * (`CANONICAL_BOT_CHAT_TITLE`) and the `handoff-<8 chars>` row a handoff writes.
 * A person never chose either, so a row or tab must not show them.
 */
export function isInternalSessionTitle(title: string): boolean {
  return title === CANONICAL_BOT_CHAT_TITLE || /^handoff-[\w-]{8}$/.test(title)
}

/** The text on one line, at most `max` characters (code points), with an ellipsis when cut. */
export function oneLineExcerpt(text: string, max = SESSION_TITLE_EXCERPT_CHARS): string {
  const collapsed = text.replace(/\s+/g, ' ').trim()
  const characters = Array.from(collapsed)

  if (characters.length <= max) {
    return collapsed
  }

  return `${characters
    .slice(0, max - 1)
    .join('')
    .trimEnd()}…`
}

/**
 * What a session row or tab calls a session: the title the person or the
 * auto-titler gave it, else the start of its first message on one line, else
 * the untitled label. Internal titles never count as a name, so they fall
 * through to the message. A real title is shown as given.
 */
export function sessionDisplayTitle(
  session: { preview?: null | string; title?: null | string },
  untitledLabel: string
): string {
  const title = session.title?.trim() ?? ''

  if (title && !isInternalSessionTitle(title)) {
    return title
  }

  return oneLineExcerpt(session.preview ?? '') || untitledLabel
}
