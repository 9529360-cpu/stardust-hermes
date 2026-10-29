import { ambientOwnerConnectionId } from '@/api/client'
import { noteActiveTreeGroup, revealTreePane } from '@/components/pane-shell/tree/store'
import { setWorkspaceScope } from '@/components/pane-shell/workspace-scope'
import type { SessionInfo } from '@/hermes'
import {
  $sessionResumeRequest,
  forgetSessionOwnerHintsForSession,
  requestSessionResume,
  sessionOwnerRouteFromRow,
  setSessionSelectionOwner
} from '@/store/session'

import { normalizeProfileKey } from '@/store/profile'

import type { OpenSessionNavigate } from '../open-session'
import { sessionRoute } from '../routes'

/** A search row is an exact user selection, including its backend/profile.
 * Resume explicitly into main: generic id-only pane reuse can front a twin on
 * another profile, and would overwrite that tile's workspace scope first. */
export function retrySessionSelection(
  sessionId: string,
  resumeSession: (sessionId: string, replaceRoute: boolean) => Promise<unknown>
): void {
  const request = $sessionResumeRequest.get()

  if (request?.sessionId === sessionId && (request.ownerRoute || request.selectedProfile)) {
    requestSessionResume(sessionId, request.ownerRoute, request.selectedProfile)
  } else {
    void resumeSession(sessionId, true)
  }
}

export function openSessionSearchResult(session: SessionInfo, navigate: OpenSessionNavigate): void {
  const ownerRoute = sessionOwnerRouteFromRow({
    ...session,
    // The ambient resolver only names local when local mode is proven. A
    // legacy remote without a registry id uses its explicit profile below.
    connection_id: session.connection_id || ambientOwnerConnectionId()
  })

  const profile = session.profile?.trim()
  const selectionOwner = ownerRoute ?? (profile ? normalizeProfileKey(profile) : null)

  setSessionSelectionOwner(session.id, selectionOwner)

  if (ownerRoute) {
    requestSessionResume(session.id, ownerRoute)
  } else if (profile) {
    requestSessionResume(session.id, undefined, profile)
  } else {
    forgetSessionOwnerHintsForSession(session.id)
    requestSessionResume(session.id)
  }

  setWorkspaceScope('sessions')
  revealTreePane('workspace')
  noteActiveTreeGroup(null)
  navigate(sessionRoute(session.id))
}
