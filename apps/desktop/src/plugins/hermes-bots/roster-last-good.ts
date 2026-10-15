import type { RosterRow } from './types'

/**
 * The last good roster rows that belong to one connection.
 *
 * While the roster query reloads (for example when the active connection id
 * first hydrates and the query key changes), there is no live data yet. Keep
 * showing this snapshot so the rows do not unmount, and never show another
 * connection's bots.
 */
export function lastRosterForConnection(
  rows: readonly RosterRow[],
  connectionId: string | null | undefined
): RosterRow[] {
  if (!connectionId) {
    return []
  }

  return rows.filter(row => row?.connectionId === connectionId)
}
