import type { ClientSessionState } from '@/app/types'
import { sessionMatchesStoredId } from '@/store/session'
import type { SessionInfo } from '@/types/hermes'

const matchesAny = (session: SessionInfo, storedIds: readonly string[]): boolean =>
  storedIds.some(storedId => sessionMatchesStoredId(session, storedId))

const stateMatchesSession = (runtimeId: string, state: ClientSessionState, session: SessionInfo): boolean =>
  sessionMatchesStoredId(session, state.storedSessionId?.trim() || runtimeId)

/** The terminal-tab fields used when resolving a task's terminal. Kept narrow
 * so the resolver does not depend on the terminal store or renderer state. */
export interface TaskTerminalEntry {
  cwd: string
  id: string
  kind: 'agent' | 'user'
  restoreCwd?: string
  storedSessionId?: null | string
}

const normalizePath = (value: string): string => {
  const trimmed = value.trim()

  return trimmed.length > 1 ? trimmed.replace(/[\\/]+$/, '') || trimmed : trimmed
}

const normalizedStoredId = (value: null | string | undefined): string => value?.trim() ?? ''

const terminalCwd = (terminal: TaskTerminalEntry): string => normalizePath(terminal.restoreCwd || terminal.cwd)

const terminalBelongsToSession = (
  terminal: TaskTerminalEntry,
  selectedStoredSessionId: string,
  sessions: readonly SessionInfo[]
): boolean => {
  const owner = normalizedStoredId(terminal.storedSessionId)

  if (!owner) {
    return false
  }

  if (owner === selectedStoredSessionId) {
    return true
  }

  const selectedSession = sessions.find(session => sessionMatchesStoredId(session, selectedStoredSessionId))

  return Boolean(selectedSession && sessionMatchesStoredId(selectedSession, owner))
}

const preferTerminal = (
  candidates: readonly TaskTerminalEntry[],
  activeTerminalId: null | string,
  targetCwd: string
): string | undefined => {
  const active = candidates.find(terminal => terminal.id === activeTerminalId)
  const activeMatchesTarget = active && (!targetCwd || terminalCwd(active) === targetCwd)

  return (
    (activeMatchesTarget ? active.id : undefined) ??
    candidates.find(terminal => targetCwd && terminalCwd(terminal) === targetCwd)?.id ??
    candidates[0]?.id
  )
}

/**
 * Resolve the user terminal that belongs with the current task context.
 *
 * An explicitly owned tab wins, including when the task's cwd has not hydrated
 * yet. Tabs without an owner are the legacy global pool and remain eligible by
 * cwd, preserving the pre-ownership behavior. A tab owned by another task is
 * never a cwd fallback, which prevents two tasks sharing a repository from
 * stealing each other's terminal. `undefined` means there is no safe tab to
 * select, so callers must leave the current selection alone.
 */
export function resolveTaskTerminalId(
  terminals: readonly TaskTerminalEntry[],
  activeTerminalId: null | string,
  selectedStoredSessionId: null | string,
  sessions: readonly SessionInfo[],
  currentCwd: string
): string | undefined {
  const userTerminals = terminals.filter(terminal => terminal.kind === 'user')
  const selected = normalizedStoredId(selectedStoredSessionId)
  const targetCwd = normalizePath(currentCwd)

  if (selected) {
    const owned = userTerminals.filter(terminal => terminalBelongsToSession(terminal, selected, sessions))

    if (owned.length > 0) {
      return preferTerminal(owned, activeTerminalId, targetCwd)
    }
  }

  // Only truly unowned tabs participate in the legacy cwd lookup. In
  // particular, never let task A's owned tab become task B's fallback.
  if (!targetCwd) {
    return undefined
  }

  const legacy = userTerminals.filter(terminal => !normalizedStoredId(terminal.storedSessionId))
  const cwdMatches = legacy.filter(terminal => terminalCwd(terminal) === targetCwd)

  return preferTerminal(cwdMatches, activeTerminalId, targetCwd)
}

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
