import { atom } from 'nanostores'

import {
  RECENT_EXPANDED_LIMIT,
  RECENT_EXPANDED_WINDOW_MS,
  stateMeta,
  type TaskRow,
  type TrackedTaskRow,
  trackTaskRows
} from './task-model'

export interface TaskHistoryScope {
  connectionId: string
  profileId: string
  sessionId: string
}

/** Process-local metadata only: no command output, credentials or disk persistence. */
export const $taskHistoryByScope = atom<Record<string, readonly TrackedTaskRow[]>>({})
const scopeKey = ({ connectionId, profileId, sessionId }: TaskHistoryScope) =>
  JSON.stringify([connectionId, profileId, sessionId])
const byNewest = (a: TrackedTaskRow, b: TrackedTaskRow) => (b.finishedAt ?? 0) - (a.finishedAt ?? 0) || b.seq - a.seq
const retained = (rows: readonly TrackedTaskRow[], now: number) =>
  rows
    .filter(row => row.finishedAt !== undefined && now - row.finishedAt <= RECENT_EXPANDED_WINDOW_MS)
    .sort(byNewest)
    .slice(0, RECENT_EXPANDED_LIMIT)

/** Capture at the producer boundary, whether or not a Tasks page is mounted. */
export function recordTaskHistory(scope: TaskHistoryScope, incoming: readonly TaskRow[], now = Date.now()) {
  if (!scope.connectionId || !scope.profileId || !scope.sessionId || scope.sessionId === '*') {
    return
  }
  const key = scopeKey(scope)
  const all = $taskHistoryByScope.get()
  const prior = all[key] ?? []
  const next = new Map(prior.map(row => [row.id, row]))
  for (const row of incoming) {
    if (!stateMeta(row.state).terminal) {
      next.delete(row.id)
      continue
    }
    // Explicit allowlist, so a producer cannot accidentally retain output or prompt payloads.
    const metadata: TaskRow = {
      id: row.id,
      rail: row.rail,
      state: row.state,
      title: row.title,
      phase: row.phase,
      sessionId: scope.sessionId,
      storedSessionId: row.storedSessionId,
      sessionTitle: row.sessionTitle,
      startedAt: row.startedAt,
      updatedAt: row.updatedAt,
      durability: 'process-local'
    }
    next.set(row.id, trackTaskRows(prior, [metadata], now)[0])
  }
  const compact: Record<string, readonly TrackedTaskRow[]> = {}
  for (const [owner, rows] of Object.entries({ ...all, [key]: [...next.values()] })) {
    const live = retained(rows, now)
    if (live.length) {
      compact[owner] = live
    }
  }
  // Bound the aggregate across scopes too; visiting many backends must not grow memory indefinitely.
  let budget = RECENT_EXPANDED_LIMIT * 4
  const ordered = Object.entries(compact).sort((a, b) => byNewest(a[1][0], b[1][0]))
  const bounded: typeof compact = {}
  for (const [owner, rows] of ordered) {
    if (!budget) {
      break
    }
    bounded[owner] = rows.slice(0, budget)
    budget -= bounded[owner].length
  }
  $taskHistoryByScope.set(bounded)
}

/** Pure read; safe during render, and never writes a store from a React render. */
export function readTaskHistory(scope: TaskHistoryScope, now = Date.now()): readonly TrackedTaskRow[] {
  if (scope.sessionId !== '*') {
    return retained($taskHistoryByScope.get()[scopeKey(scope)] ?? [], now)
  }
  const rows = Object.entries($taskHistoryByScope.get())
    .filter(([key]) => {
      const [connectionId, profileId] = JSON.parse(key) as string[]
      return connectionId === scope.connectionId && profileId === scope.profileId
    })
    .flatMap(([, values]) => values)
  return retained(rows, now)
}

export function clearTaskHistory(scope: TaskHistoryScope) {
  const all = { ...$taskHistoryByScope.get() }
  delete all[scopeKey(scope)]
  $taskHistoryByScope.set(all)
}
