import { describe, expect, it } from 'vitest'

import type { SessionOwnerRoute } from '@/store/session-request-router'
import { makeSessionInfo } from '@/test/session-info'

import { isSelectedSessionRow, selectedSessionIdentityKey } from './session-selection'

const row = (profile: string, connectionId = 'local') =>
  makeSessionInfo({ connection_id: connectionId, id: 'shared-session', profile })

describe('owner-aware sidebar selection', () => {
  it('highlights only the selected profile when another profile has the same stored id', () => {
    const research = row('research')
    const defaultProfile = makeSessionInfo({ id: 'shared-session', profile: 'default' })
    const identityKey = selectedSessionIdentityKey([research, defaultProfile], 'shared-session', {
      connectionId: 'local',
      profile: 'default'
    })

    expect(isSelectedSessionRow(defaultProfile, 'shared-session', identityKey)).toBe(true)
    expect(isSelectedSessionRow(research, 'shared-session', identityKey)).toBe(false)
  })

  it('paints an explicit owner before its row returns in the active list', () => {
    const owner: SessionOwnerRoute = { connectionId: 'local', profile: 'research' }

    expect(selectedSessionIdentityKey([], 'shared-session', owner, true)).toBe(
      selectedSessionIdentityKey([row('research')], 'shared-session', owner)
    )
  })

  it('uses a unique cached row owner when no explicit selection owner is present', () => {
    const research = makeSessionInfo({ id: 'shared-session', profile: 'research' })

    expect(selectedSessionIdentityKey([research], 'shared-session', undefined)).toBe(
      selectedSessionIdentityKey([research], 'shared-session', { connectionId: 'local', profile: 'research' })
    )
  })

  it('does not guess when cached rows disagree about an owner', () => {
    const rows = [row('research'), row('default')]

    expect(selectedSessionIdentityKey(rows, 'shared-session', undefined)).toBeNull()
  })

  it('resolves a unique registry owner before its row receives a connection tag', () => {
    const legacyLocal = makeSessionInfo({ id: 'shared-session', profile: 'default' })
    const identityKey = selectedSessionIdentityKey([legacyLocal], 'shared-session', {
      connectionId: 'local',
      profile: 'default'
    })

    expect(isSelectedSessionRow(legacyLocal, 'shared-session', identityKey)).toBe(true)
  })

  it('uses a profile-only owner when one matching legacy row confirms it', () => {
    const legacyLocal = makeSessionInfo({ id: 'shared-session', profile: 'default' })
    const identityKey = selectedSessionIdentityKey([legacyLocal], 'shared-session', 'default')

    expect(isSelectedSessionRow(legacyLocal, 'shared-session', identityKey)).toBe(true)
  })

  it('does not infer a profile-only owner across connections', () => {
    expect(selectedSessionIdentityKey([row('default', 'homelab')], 'shared-session', 'default')).toBeNull()
  })

  it('does not infer a profile-only owner from a mixed tagged and untagged cache', () => {
    const legacyLocal = makeSessionInfo({ id: 'shared-session', profile: 'default' })
    const remote = row('default', 'homelab')

    expect(selectedSessionIdentityKey([legacyLocal, remote], 'shared-session', 'default')).toBeNull()
  })

  it('distinguishes same-profile sessions owned by separate registry connections', () => {
    const local = row('default', 'local')
    const remote = row('default', 'homelab')
    const identityKey = selectedSessionIdentityKey([local, remote], 'shared-session', {
      connectionId: 'homelab',
      profile: 'default'
    })

    expect(isSelectedSessionRow(remote, 'shared-session', identityKey)).toBe(true)
    expect(isSelectedSessionRow(local, 'shared-session', identityKey)).toBe(false)
  })
})
