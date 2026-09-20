import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import type { DelegationRecoveryReceipt } from '@/store/activity'
import { $gatewayState } from '@/store/session'
import { knownOwnerForSession, requestForOwnedSession } from '@/store/session-states'

const rejectAmbientRecoveryRequest = async <T>(): Promise<T> => {
  throw new Error('Delegation recovery owner unavailable')
}

interface RecoverySnapshot {
  receipts: DelegationRecoveryReceipt[]
  sessionId: null | string
}

/**
 * Read recent process-loss receipts from the session's authoritative backend.
 *
 * The snapshot is renderer-local and disposable. The durable async-delegation
 * ledger remains the only owner; this hook never acknowledges, retries, or
 * mutates a background task.
 */
export function useDelegationRecovery(sessionId: null | string): DelegationRecoveryReceipt[] {
  const gatewayState = useStore($gatewayState)
  const [snapshot, setSnapshot] = useState<RecoverySnapshot>({ receipts: [], sessionId: null })

  useEffect(() => {
    if (!sessionId) {
      return
    }

    let cancelled = false
    let pending = false

    const refresh = async () => {
      if (cancelled || pending) {
        return
      }

      pending = true
      const owner = JSON.stringify(knownOwnerForSession(sessionId))

      try {
        const result = await requestForOwnedSession<{ receipts: DelegationRecoveryReceipt[] }>(
          sessionId,
          rejectAmbientRecoveryRequest,
          'delegation.recovery.list',
          { session_id: sessionId }
        )

        if (!cancelled && owner === JSON.stringify(knownOwnerForSession(sessionId))) {
          setSnapshot({
            receipts: Array.isArray(result.receipts) ? result.receipts : [],
            sessionId
          })
        }
      } catch {
        // Older backends simply have no recovery surface. A reconnect or focus
        // event will retry without polling the durable ledger continuously.
      } finally {
        pending = false
      }
    }

    void refresh()
    const onFocus = () => void refresh()
    window.addEventListener('focus', onFocus)

    return () => {
      cancelled = true
      window.removeEventListener('focus', onFocus)
    }
  }, [gatewayState, sessionId])

  return snapshot.sessionId === sessionId ? snapshot.receipts : []
}
