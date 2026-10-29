import { sessionIdentityKey, sessionOwnerIdentityKey } from '@/lib/session-identity'
import type { SessionOwnerRoute } from '@/store/session-request-router'
import type { SessionInfo } from '@/types/hermes'

export type SessionSelectionOwner = null | SessionOwnerRoute | string | undefined

type SessionSelectionRow = Pick<SessionInfo, '_lineage_ids' | '_lineage_root_id' | 'connection_id' | 'id' | 'profile'>

function rowRepresentsStoredId(row: SessionSelectionRow, storedSessionId: string): boolean {
  return (
    row.id === storedSessionId ||
    row._lineage_root_id === storedSessionId ||
    Boolean(row._lineage_ids?.includes(storedSessionId))
  )
}

function identityKeyForOwner(storedSessionId: string, owner: SessionOwnerRoute | string): string {
  if (typeof owner === 'string') {
    return sessionIdentityKey({ id: storedSessionId, profile: owner })
  }

  return sessionIdentityKey({
    id: storedSessionId,
    connection_id: owner.connectionId,
    profile: owner.profile
  })
}

function rowMatchesOwner(row: SessionSelectionRow, owner: SessionOwnerRoute): boolean {
  const connectionId = row.connection_id?.trim()
  const profile = owner.profile.trim() || 'default'

  if ((row.profile?.trim() || 'default') !== profile) {
    return false
  }

  if (connectionId) {
    return connectionId === owner.connectionId.trim()
  }

  // Local aggregates omit their connection tag. A unique untagged profile row
  // can still use its row identity after an explicit local selection.
  return owner.connectionId.trim() === 'local'
}

/** Resolve the selected row only when its owner is known or unambiguous. */
export function selectedSessionIdentityKey(
  rows: readonly SessionSelectionRow[],
  storedSessionId: null | string,
  owner: SessionSelectionOwner,
  explicit = false
): null | string {
  if (!storedSessionId) {
    return null
  }

  const matchingRows = rows.filter(row => rowRepresentsStoredId(row, storedSessionId))

  if (explicit && !owner) {
    return null
  }

  if (owner) {
    const expected = identityKeyForOwner(storedSessionId, owner)

    if (typeof owner === 'string') {
      if (matchingRows.some(row => row.connection_id?.trim())) {
        return null
      }

      const profile = owner.trim() || 'default'
      const ownedRows = matchingRows.filter(
        row => !row.connection_id?.trim() && (row.profile?.trim() || 'default') === profile
      )
      const identities = new Set(ownedRows.map(row => sessionIdentityKey(row, storedSessionId)))

      if (identities.size === 1) {
        return [...identities][0]
      }

      // A profile-only owner cannot identify a row when the same id appears
      // on another source or when no row confirms this profile.
      return null
    }

    const ownerRows = matchingRows.filter(row => rowMatchesOwner(row, owner))
    const identities = new Set(ownerRows.map(row => sessionIdentityKey(row, storedSessionId)))

    if (identities.size === 1) {
      return [...identities][0]
    }

    // A click carries the owner before its row may reappear in the active slice.
    return identities.size === 0 && (explicit || matchingRows.length === 0) ? expected : null
  }

  const owners = new Set(matchingRows.map(sessionOwnerIdentityKey))

  if (owners.size === 1 && matchingRows[0]) {
    return sessionIdentityKey(matchingRows[0], storedSessionId)
  }

  return null
}

export function isSelectedSessionRow(
  row: SessionSelectionRow,
  activeSessionId: null | string,
  activeIdentityKey?: null | string
): boolean {
  if (!activeSessionId) {
    return false
  }

  // Standalone legacy callers without owner context keep their id-only behavior.
  if (activeIdentityKey === undefined) {
    return row.id === activeSessionId
  }

  return (
    activeIdentityKey !== null &&
    rowRepresentsStoredId(row, activeSessionId) &&
    sessionIdentityKey(row, activeSessionId) === activeIdentityKey
  )
}
