import { describe, expect, it } from 'vitest'

import { lastRosterForConnection } from './roster-last-good'
import type { RosterRow } from './types'

const row = (connectionId: string, name: string) => ({ connectionId, name }) as RosterRow

describe('lastRosterForConnection', () => {
  it('keeps the rows of the active connection while the roster reloads', () => {
    const rows = [row('local', 'alpha'), row('local', 'beta')]

    expect(lastRosterForConnection(rows, 'local')).toEqual(rows)
  })

  it("never returns another connection's bots", () => {
    const rows = [row('local', 'alpha'), row('remote', 'gamma')]

    expect(lastRosterForConnection(rows, 'remote')).toEqual([row('remote', 'gamma')])
  })

  it('returns nothing when no connection is active', () => {
    expect(lastRosterForConnection([row('local', 'alpha')], undefined)).toEqual([])
    expect(lastRosterForConnection([row('local', 'alpha')], '')).toEqual([])
  })
})
