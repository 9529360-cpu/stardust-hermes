import { describe, expect, it } from 'vitest'

import { makeSessionInfo } from '@/test/session-info'

import { deduplicateSessionRows, sessionIdentityKey } from './session-identity'

const row = (id: string, overrides: Parameters<typeof makeSessionInfo>[0] = {}) => makeSessionInfo({ id, ...overrides })

describe('session identity', () => {
  it('keeps equal stored ids owned by different profiles', () => {
    const research = row('shared', { profile: 'research' })
    const defaultProfile = row('shared', { profile: 'default' })

    expect(sessionIdentityKey(research)).not.toBe(sessionIdentityKey(defaultProfile))
    expect(deduplicateSessionRows([research, defaultProfile])).toEqual([research, defaultProfile])
  })

  it('keeps equal profile ids owned by different registry connections', () => {
    const local = row('shared', { connection_id: 'local', profile: 'default' })
    const remote = row('shared', { connection_id: 'homelab', profile: 'default' })

    expect(deduplicateSessionRows([local, remote])).toEqual([local, remote])
  })

  it('merges duplicate rows and compression aliases only within one owner', () => {
    const recent = row('tip', { _lineage_root_id: 'root', connection_id: 'local', profile: 'research' })
    const messagingDuplicate = row('root', { connection_id: 'local', profile: 'research', source: 'telegram' })
    const otherProfileTwin = row('root', { connection_id: 'local', profile: 'default' })

    expect(deduplicateSessionRows([recent, messagingDuplicate, otherProfileTwin])).toEqual([recent, otherProfileTwin])
  })
})
