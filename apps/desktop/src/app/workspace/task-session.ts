import type { ClientSessionState } from '@/app/types'
import { sessionMatchesStoredId } from '@/store/session'
import type { SessionInfo } from '@/types/hermes'

const matchesAny = (session: SessionInfo, storedIds: readonly string[]): boolean =>
  storedIds.some(storedId => sessionMatchesStoredId(session, storedId))

const stateMatchesSession = (runtimeId: string, state: ClientSessionState, session: SessionInfo): boolean =>
  sessionMatchesStoredId(session, state.storedSessionId?.trim() || runtimeId)

/**
 * Pick the live task that deserves a primary "continue" entry when no
 * conversation is currently selected. A task blocked on user input outranks a
 * merely running task because the user can unblock it immediately.
 */
export function findLiveTaskSession(
  sessions: readonly SessionInfo[],
  attentionSessionIds: readonly string[],
  workingSessionIds: readonly string[]
): SessionInfo | undefined {
  return (
    sessions.find(session => matchesAny(session, attentionSessionIds)) ??
    sessions.find(session => matchesAny(session, workingSessionIds))
  )
}

/**
 * Resolve the durable route id for the primary live task. Session metadata can
 * lag the runtime/attention projection during resume. In that hydration gap,
 * keep the real projected id instead of degrading a "continue task" action
 * into the new-chat route.
 */
export function findLiveTaskStoredId(
  sessions: readonly SessionInfo[],
  attentionSessionIds: readonly string[],
  workingSessionIds: readonly string[]
): string | null {
  return (
    findLiveTaskSession(sessions, attentionSessionIds, workingSessionIds)?.id ??
    attentionSessionIds[0] ??
    workingSessionIds[0] ??
    null
  )
}

/**
 * Resolve the live runtime that currently owns a stored Task Thread. Status
 * stacks (todos/subagents/background work) are keyed by runtime id, while the
 * Workspace chooses tasks by durable stored ids. Prefer the same lifecycle
 * ordering as the task picker: needs-input first, then busy, then any matching
 * retained runtime as a last-resort hydration bridge.
 */
export function findLiveTaskRuntimeId(
  states: Readonly<Record<string, ClientSessionState>>,
  session: SessionInfo
): string | null {
  const entries = Object.entries(states).filter(([runtimeId, state]) => stateMatchesSession(runtimeId, state, session))

  return (
    entries.find(([, state]) => state.needsInput)?.[0] ??
    entries.find(([, state]) => state.busy || state.awaitingResponse)?.[0] ??
    entries[0]?.[0] ??
    null
  )
}

/**
 * Hydration-gap variant when the durable session row has not arrived yet.
 * Exact stored-id/runtime-id equality is enough here because there is no
 * lineage metadata to safely broaden the match.
 */
export function findLiveTaskRuntimeIdByStoredId(
  states: Readonly<Record<string, ClientSessionState>>,
  storedSessionId: string
): string | null {
  const entries = Object.entries(states).filter(
    ([runtimeId, state]) => state.storedSessionId?.trim() === storedSessionId || runtimeId === storedSessionId
  )

  return (
    entries.find(([, state]) => state.needsInput)?.[0] ??
    entries.find(([, state]) => state.busy || state.awaitingResponse)?.[0] ??
    entries[0]?.[0] ??
    null
  )
}

/**
 * Resolve the coding context for Workspace.
 *
 * A selected conversation owns the live/transient cwd. With no selected
 * stored conversation, however, a recovered background Task Thread becomes
 * Workspace's primary context, so its durable cwd must outrank an unrelated
 * blank draft's default cwd (for example the user's home directory).
 */
export function resolveTaskWorkspaceCwd(
  currentCwd: string,
  selectedSession: SessionInfo | undefined,
  fallbackTaskSession: SessionInfo | undefined,
  scopedProjectCwd: string
): string {
  const live = currentCwd.trim()
  const selected = selectedSession?.cwd?.trim() ?? ''
  const fallback = fallbackTaskSession?.cwd?.trim() ?? ''
  const scoped = scopedProjectCwd.trim()

  if (selectedSession) {
    return live || selected || scoped
  }

  if (fallbackTaskSession) {
    return fallback || live || scoped
  }

  return live || scoped
}
