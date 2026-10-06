import { useEffect, useState } from 'react'

import type { UsageStats } from '@/types/hermes'

interface SessionTokenUsageOptions {
  busy: boolean
  enabled: boolean
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  sessionId: null | string
}

/** Fetch cumulative, backend-owned counters for the selected runtime session.
 * Key the result to its owner so a tab switch cannot show the previous chat's
 * usage; refresh on turn completion instead of polling during streaming. */
export function useSessionTokenUsage({ busy, enabled, requestGateway, sessionId }: SessionTokenUsageOptions) {
  const [fetched, setFetched] = useState<{ sessionId: string; usage: UsageStats } | null>(null)
  const [loadingFor, setLoadingFor] = useState<null | string>(null)

  useEffect(() => {
    if (!enabled || !sessionId || busy) {
      return
    }

    let cancelled = false
    setLoadingFor(sessionId)

    void requestGateway<UsageStats>('session.usage', { session_id: sessionId })
      .then(usage => {
        if (!cancelled && usage) {
          setFetched({ sessionId, usage })
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFetched(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingFor(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [busy, enabled, requestGateway, sessionId])

  return fetched?.sessionId === sessionId && enabled && !busy && loadingFor !== sessionId ? fetched.usage : null
}
