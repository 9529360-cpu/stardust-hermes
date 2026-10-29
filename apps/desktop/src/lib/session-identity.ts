import { LOCAL_CONNECTION_ID, registryBackendScopeKey } from '@stardust/shared'

import type { SessionInfo } from '@/types/hermes'

type SessionIdentity = Pick<SessionInfo, '_lineage_ids' | '_lineage_root_id' | 'connection_id' | 'id' | 'profile'>

export function sessionOwnerIdentityKey(session: SessionIdentity): string {
  const connectionId = session.connection_id?.trim()

  return registryBackendScopeKey(
    connectionId === LOCAL_CONNECTION_ID ? null : connectionId,
    (session.profile ?? '').trim() || 'default'
  )
}

export function sessionIdentityKey(session: SessionIdentity, id = session.id): string {
  return JSON.stringify([sessionOwnerIdentityKey(session), id])
}

/** Identities this row can represent across compression, scoped to its owner. */
export function sessionIdentityKeys(session: SessionIdentity): string[] {
  const ids = new Set<string>(
    [session.id, session._lineage_root_id ?? '', ...(session._lineage_ids ?? [])].filter(Boolean)
  )

  return [...ids].map(id => sessionIdentityKey(session, id))
}

export function sessionRowsMatchIdentity(left: SessionIdentity, right: SessionIdentity): boolean {
  if (sessionOwnerIdentityKey(left) !== sessionOwnerIdentityKey(right)) {
    return false
  }

  const rightIds = new Set(sessionIdentityKeys(right))

  return sessionIdentityKeys(left).some(identity => rightIds.has(identity))
}

/** Keep the first row for each owner-qualified id or compression lineage. */
export function deduplicateSessionRows(
  sessions: readonly SessionInfo[],
  rowsMatch: (left: SessionInfo, right: SessionInfo) => boolean = sessionRowsMatchIdentity
): SessionInfo[] {
  const rows: SessionInfo[] = []

  for (const session of sessions) {
    if (!rows.some(existing => rowsMatch(existing, session))) {
      rows.push(session)
    }
  }

  return rows
}
