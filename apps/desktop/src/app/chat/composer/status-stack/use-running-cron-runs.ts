import type { CronExecutionsListResult, WorkItem } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'

const POLL_MS = 5000
const LIMIT = 20
const MAX_FAILURES = 3

/**
 * Running background cron runs for the active profile, read-only (`cron.executions.list`).
 *
 * The launch profile is requested as '' (the same encoding cron.manage uses), so the backend's
 * `scoped` echo must match the request exactly. A response scoped to another profile is dropped whole.
 */
export function useRunningCronRuns(enabled: boolean): WorkItem[] {
  const { requestGateway } = useGatewayRequest()
  const profile = normalizeProfileKey(useStore($activeGatewayProfile))
  const requested = profile === 'default' ? '' : profile
  const [runs, setRuns] = useState<WorkItem[]>([])

  useEffect(() => {
    if (!enabled) {
      setRuns([])

      return
    }

    let cancelled = false
    let pending = false
    let failures = 0
    let timer: number | undefined

    const refresh = async () => {
      if (cancelled || pending) {
        return
      }

      pending = true

      try {
        const result = await requestGateway<CronExecutionsListResult>('cron.executions.list', {
          profile: requested,
          limit: LIMIT
        })

        if (cancelled) {
          return
        }

        const next = (result.scoped ?? '') === requested
          ? (result.work ?? []).filter(item => item.status === 'running')
          : []

        setRuns(current => (JSON.stringify(current) === JSON.stringify(next) ? current : next))
        failures = 0
      } catch {
        if (!cancelled) {
          failures++
          setRuns([])
        }
      } finally {
        pending = false
      }

      if (!cancelled && failures < MAX_FAILURES) {
        timer = window.setTimeout(() => void refresh(), POLL_MS)
      }
    }

    const retry = () => {
      failures = 0
      window.clearTimeout(timer)
      void refresh()
    }

    void refresh()
    window.addEventListener('focus', retry)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
      window.removeEventListener('focus', retry)
    }
  }, [enabled, requested, requestGateway])

  return runs
}
