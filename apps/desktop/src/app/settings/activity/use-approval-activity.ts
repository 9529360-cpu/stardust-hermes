import type {
  ApprovalAuditEntry,
  ApprovalAuditResult,
  ApprovalGrant,
  ApprovalGrantsListResult,
  ApprovalGrantsRevokeResult
} from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { $settingsRequestProfile } from '@/store/settings-scope'

export const APPROVAL_AUDIT_LIMIT = 50

export type ApprovalActivityStatus = 'loading' | 'ready' | 'error'

/**
 * Recent approval decisions and standing grants for the profile the settings page targets.
 *
 * The backend is the authority; this list is a cache that reloads after every write. A revoke
 * removes its row at once and the authoritative reload restores it if the backend did not revoke it.
 * Responses from an earlier load never overwrite a newer one.
 */
export function useApprovalActivity() {
  const { requestGateway } = useGatewayRequest()
  const scopeProfile = useStore($settingsRequestProfile)
  const [decisions, setDecisions] = useState<ApprovalAuditEntry[]>([])
  const [grants, setGrants] = useState<ApprovalGrant[]>([])
  const [status, setStatus] = useState<ApprovalActivityStatus>('loading')
  const [revokeFailed, setRevokeFailed] = useState(false)
  const loadGeneration = useRef(0)

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current
    const isCurrent = () => generation === loadGeneration.current
    const profileParams = scopeProfile ? { profile: scopeProfile } : {}

    // Each read lands on its own, so grants can show while decisions are still loading.
    const auditRead = requestGateway<ApprovalAuditResult>('approval.audit', { limit: APPROVAL_AUDIT_LIMIT }).then(
      result => {
        if (isCurrent()) {
          setDecisions(result.entries ?? [])
        }

        return true
      },
      () => false
    )

    const grantsRead = requestGateway<ApprovalGrantsListResult>('approval.grants.list', profileParams).then(
      result => {
        if (isCurrent()) {
          setGrants(result.grants ?? [])
        }

        return true
      },
      () => false
    )

    const [auditOk, grantsOk] = await Promise.all([auditRead, grantsRead])

    if (isCurrent()) {
      setStatus(auditOk && grantsOk ? 'ready' : 'error')
    }
  }, [requestGateway, scopeProfile])

  const revoke = useCallback(
    async (grant: ApprovalGrant) => {
      setRevokeFailed(false)
      setGrants(current => current.filter(item => item.id !== grant.id))

      try {
        const result = await requestGateway<ApprovalGrantsRevokeResult>('approval.grants.revoke', {
          ...(scopeProfile ? { profile: scopeProfile } : {}),
          id: grant.id
        })

        setRevokeFailed(!result.revoked)
      } catch {
        setRevokeFailed(true)
      }

      // The authoritative list gets the last word: it restores the row if the revoke did not land.
      await load()
    },
    [load, requestGateway, scopeProfile]
  )

  useEffect(() => {
    void load()
  }, [load])

  return { decisions, grants, status, revokeFailed, reload: load, revoke }
}
