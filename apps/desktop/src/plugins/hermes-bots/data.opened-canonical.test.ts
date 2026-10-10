/**
 * An open of a bot's Bot Chat teaches the roster snapshot which registry row
 * that bot owns.
 *
 * The snapshot only refreshes while the roster page is mounted. A chat created
 * after the last fetch is therefore missing from `canonical_session`, and every
 * reader of that field (the empty chat state, the /new guard, the Routines
 * owner) treats the bot as chatless until the page reopens. The open already
 * resolved the registry row, so its answer is written back here.
 */

import { describe, expect, it, vi } from 'vitest'

import type { RosterRow } from './types'

vi.mock('@hermes/plugin-sdk', async () => {
  const { atom } = await import('nanostores')

  return {
    atom,
    host: { state: { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } } },
    queryClient: {
      getQueriesData: () => [],
      getQueryData: () => undefined,
      invalidateQueries: vi.fn(),
      setQueryData: vi.fn()
    },
    useQuery: vi.fn(),
    useValue: vi.fn()
  }
})

vi.mock('./shared', () => ({ getPluginCtx: () => null, ID: 'hermes-bots' }))

async function loadData() {
  vi.resetModules()

  return import('./data')
}

const alpha = (canonical: RosterRow['canonical_session'] = null): RosterRow => ({
  canonical_session: canonical,
  connectionId: 'local',
  name: 'alpha'
})

const beta = (): RosterRow => ({ connectionId: 'local', name: 'beta' })

describe('recordOpenedCanonicalChat', () => {
  it('names the registry row and its lineage tip on the bot that opened it', async () => {
    const { $lastRoster, recordOpenedCanonicalChat } = await loadData()
    $lastRoster.set([alpha(), beta()])

    recordOpenedCanonicalChat('local::alpha', { openedId: 'tip-1', registryId: 'reg-1' })

    const [opened, second] = $lastRoster.get()

    expect(opened.canonical_session).toEqual({ id: 'reg-1', resolved_id: 'tip-1' })
    expect(second).toEqual(beta())
  })

  it('leaves the other rows of the snapshot as the same objects', async () => {
    const { $lastRoster, recordOpenedCanonicalChat } = await loadData()
    const other = beta()
    $lastRoster.set([alpha(), other])

    recordOpenedCanonicalChat('local::alpha', { openedId: 'reg-1', registryId: 'reg-1' })

    expect($lastRoster.get()[1]).toBe(other)
  })

  it('is a no-op that keeps the snapshot array when it already names that row', async () => {
    const { $lastRoster, recordOpenedCanonicalChat } = await loadData()
    const rows = [alpha({ id: 'reg-1', preview: 'hi', resolved_id: 'tip-1' }), beta()]
    $lastRoster.set(rows)
    const before = $lastRoster.get()

    recordOpenedCanonicalChat('local::alpha', { openedId: 'tip-1', registryId: 'reg-1' })

    expect($lastRoster.get()).toBe(before)
  })

  it('keeps the registry row own preview while the same row is reopened at a newer tip', async () => {
    const { $lastRoster, recordOpenedCanonicalChat } = await loadData()
    $lastRoster.set([alpha({ id: 'reg-1', last_active: 42, preview: 'last words', resolved_id: 'tip-1' })])

    recordOpenedCanonicalChat('local::alpha', { openedId: 'tip-2', registryId: 'reg-1' })

    expect($lastRoster.get()[0].canonical_session).toEqual({
      id: 'reg-1',
      last_active: 42,
      preview: 'last words',
      resolved_id: 'tip-2'
    })
  })

  it('does not carry another registry row’s preview onto a different row', async () => {
    const { $lastRoster, recordOpenedCanonicalChat } = await loadData()
    $lastRoster.set([alpha({ id: 'reg-old', preview: 'stale', resolved_id: 'tip-old' })])

    recordOpenedCanonicalChat('local::alpha', { openedId: 'reg-new', registryId: 'reg-new' })

    expect($lastRoster.get()[0].canonical_session).toEqual({ id: 'reg-new', resolved_id: 'reg-new' })
  })

  it('does nothing for a bot that is not in the snapshot', async () => {
    const { $lastRoster, recordOpenedCanonicalChat } = await loadData()
    const rows = [beta()]
    $lastRoster.set(rows)

    recordOpenedCanonicalChat('local::ghost', { openedId: 'tip-1', registryId: 'reg-1' })

    expect($lastRoster.get()).toBe(rows)
  })
})
