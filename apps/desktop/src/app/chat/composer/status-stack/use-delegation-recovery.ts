import { useStore } from '@nanostores/react'
import type { DelegationRecoveryListResult, DelegationRecoveryReceipt } from '@stardust/shared'
import { useEffect, useState } from 'react'

import { usePaneVisible } from '@/components/pane-shell/pane-visibility'
import { isMissingRpcMethod } from '@/lib/gateway-rpc'
import { $gatewayState } from '@/store/session'
import { knownOwnerForSession, requestForOwnedSession } from '@/store/session-states'

import { rejectUnownedSubagentRequest } from './use-subagent-snapshot'

// Receipts appear only when a background owner process dies, so this is a safety net, not a live feed.
const POLL_MS = 60_000
// A transient failure is retried after each of these gaps, then the hook waits for a wake signal
// (window focus / visibility, gateway change, session or pane change) instead of looping.
const RETRY_GAPS_MS = [3_000, 10_000, 30_000]

interface RecoveryState {
  /** Owner the receipts were read from; another owner's receipts say nothing about this one. */
  owner: string
  receipts: DelegationRecoveryReceipt[]
  sessionId: string
}

const NONE: DelegationRecoveryReceipt[] = []

const ownerKey = (sessionId: string) => JSON.stringify(knownOwnerForSession(sessionId)) ?? ''

/** Only rows the backend itself marked unknown: this surface has no honest copy for any other state. */
function unknownReceipts(result: DelegationRecoveryListResult | null | undefined): DelegationRecoveryReceipt[] {
  const rows = result?.receipts

  return Array.isArray(rows)
    ? rows.filter(
        row => Boolean(row) && row.state === 'unknown' && typeof row.delegation_id === 'string' && typeof row.goal === 'string'
      )
    : NONE
}

/** Keep the previous reference when a re-read changed nothing, and stay empty for an empty first read. */
function settle(previous: null | RecoveryState, next: RecoveryState): null | RecoveryState {
  if (!previous) {
    return next.receipts.length > 0 ? next : previous
  }

  return previous.sessionId === next.sessionId &&
    previous.owner === next.owner &&
    JSON.stringify(previous.receipts) === JSON.stringify(next.receipts)
    ? previous
    : next
}

/**
 * Unknown-outcome receipts for background delegations whose owner process exited (`delegation.recovery.list`).
 *
 * Display-only and separate from the live roster (`useSubagentSnapshot`): it never writes the subagent store
 * and nothing here re-dispatches work. The read goes to the session's OWNER route with only `session_id`, so a
 * receipt can never come from a backend the session does not live on; an answer whose owner changed in flight
 * is dropped. An older backend without the RPC is a silent no-op (one probe per gateway), transient failures
 * retry a bounded number of times, and nothing is requested while the pane or window is hidden.
 */
export function useDelegationRecovery(sessionId: null | string): DelegationRecoveryReceipt[] {
  const gatewayState = useStore($gatewayState)
  const paneVisible = usePaneVisible()
  const [state, setState] = useState<null | RecoveryState>(null)

  useEffect(() => {
    if (!sessionId || !paneVisible) {
      return
    }

    let cancelled = false
    let pending = false
    let unsupported = false
    let failures = 0
    let timer: number | undefined

    const schedule = (ms: number) => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => void refresh(), ms)
    }

    const fail = () => {
      failures++

      if (failures <= RETRY_GAPS_MS.length) {
        schedule(RETRY_GAPS_MS[failures - 1])
      }
    }

    const refresh = async () => {
      if (cancelled || pending || unsupported || document.visibilityState === 'hidden') {
        return
      }

      pending = true
      const owner = ownerKey(sessionId)

      // Receipts read under another session or owner are not this one's truth: forget them before asking again.
      setState(previous => (previous && (previous.sessionId !== sessionId || previous.owner !== owner) ? null : previous))

      try {
        const result = await requestForOwnedSession<DelegationRecoveryListResult>(
          sessionId,
          rejectUnownedSubagentRequest,
          'delegation.recovery.list',
          { session_id: sessionId }
        )

        if (cancelled) {
          return
        }

        if (owner !== ownerKey(sessionId)) {
          // The owner moved while this was in flight: the answer is from a backend the session no longer lives on.
          fail()

          return
        }

        const receipts = unknownReceipts(result)

        setState(previous => settle(previous, { owner, receipts, sessionId }))
        failures = 0
        schedule(POLL_MS)
      } catch (error) {
        if (cancelled) {
          return
        }

        if (isMissingRpcMethod(error)) {
          // The backend predates this RPC: nothing a retry can fix and nothing to tell the user.
          // A gateway change re-runs this effect and probes the (possibly upgraded) backend again.
          unsupported = true
          window.clearTimeout(timer)
          setState(null)

          return
        }

        fail()
      } finally {
        pending = false
      }
    }

    const wake = () => {
      failures = 0
      void refresh()
    }

    window.addEventListener('focus', wake)
    document.addEventListener('visibilitychange', wake)
    void refresh()

    return () => {
      cancelled = true
      window.clearTimeout(timer)
      window.removeEventListener('focus', wake)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [sessionId, gatewayState, paneVisible])

  return state?.sessionId === sessionId ? state.receipts : NONE
}
