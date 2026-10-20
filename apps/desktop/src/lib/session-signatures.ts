/**
 * Cheap signature compares for poll loops — swap the atom (and re-render)
 * only when the rows/transcript actually changed.
 */

import type { SessionInfo, SessionMessage } from '@/hermes'

export function sameCronSignature(a: SessionInfo[], b: SessionInfo[]): boolean {
  if (a.length !== b.length) {
    return false
  }

  return a.every((session, i) => {
    const other = b[i]

    return (
      other != null &&
      session.id === other.id &&
      session._lineage_root_id === other._lineage_root_id &&
      session.title === other.title &&
      session.source === other.source &&
      session.profile === other.profile &&
      session.preview === other.preview &&
      session.message_count === other.message_count &&
      session.last_active === other.last_active &&
      session.ended_at === other.ended_at &&
      // Row STATE, not just row content: session-pin-sync reconciles the
      // sidebar's pins against `pinned` on the rows in this atom, so a page
      // whose only delta is a flag has to swap in or the reconciler reads a
      // frozen copy forever. An idle conversation never moves any of the
      // fields above again, which is exactly when a pin gets toggled (#76919).
      session.pinned === other.pinned &&
      session.archived === other.archived
    )
  })
}

// FNV-1a over every field transcript hydration (toChatMessages) renders from.
function hashString(hash: number, value: string): number {
  let next = hash

  for (let i = 0; i < value.length; i++) {
    next ^= value.charCodeAt(i)
    next = Math.imul(next, 16777619)
  }

  return next >>> 0
}

// Every SessionMessage field that changes what chat-messages/hydration.ts
// paints. Hashing only role/timestamp/content let a row whose visible text
// lives in `display_content` or the `codex_message_items` sidecar (or whose
// reasoning/tool calls landed in a later write) read as "unchanged", so the
// poll skipped it and the reply only appeared after a restart.
const DISPLAY_FIELDS = [
  'role',
  'timestamp',
  'id',
  'row_id',
  'content',
  'display_content',
  'display_kind',
  'display_metadata',
  'text',
  'context',
  'name',
  'reasoning',
  'reasoning_content',
  'reasoning_details',
  'codex_message_items',
  'tool_calls',
  'tool_call_id',
  'tool_name',
  'args'
] as const satisfies readonly (keyof SessionMessage)[]

function fieldText(value: unknown): string {
  if (value === undefined) {
    return '\u0000u'
  }

  return typeof value === 'string' ? value : (JSON.stringify(value) ?? '')
}

/** Transcript fingerprint for the active-messaging-session poll. */
export function sessionMessagesSignature(messages: SessionMessage[]): string {
  let hash = 2166136261

  for (const m of messages) {
    for (const field of DISPLAY_FIELDS) {
      hash = hashString(hash, fieldText(m[field]))
      // Field separator so shifting text between adjacent fields still changes the hash.
      hash = hashString(hash, '\u0001')
    }
  }

  return `${messages.length}:${hash}`
}
