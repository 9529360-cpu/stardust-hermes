import type { CronExecutionsListResult } from '@hermes/shared'
import { useEffect, useState } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'

import { type RecentRun, sortRunsNewestFirst } from './recent-runs'

const POLL_MS = 8000
const LIMIT = 30
const MAX_FAILURES = 3
const SEPARATOR = '\u0001'

/**
 * Recent scheduled runs for the given profiles (`cron.executions.list`, read-only).
 *
 * A profile is requested as '' when it is the launch profile, the same encoding cron.manage uses,
 * and a response whose `scoped` echo does not match the request is dropped. If a poll fails, the last
 * good list stays on screen. After repeated failures polling stops, and focusing the window retries.
 */
export function useRecentCronRuns(profiles: readonly string[], enabled: boolean): RecentRun[] {
  const { requestGateway } = useGatewayRequest()
  const profilesKey = profiles.join(SEPARATOR)
  const [runs, setRuns] = useState<RecentRun[]>([])

  useEffect(() => {
    const names = profilesKey ? profilesKey.split(SEPARATOR) : []

    if (!enabled || names.length === 0) {
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
        const batches = await Promise.all(
          names.map(async name => {
            const requested = name === 'default' ? '' : name

            const result = await requestGateway<CronExecutionsListResult>('cron.executions.list', {
              profile: requested,
              limit: LIMIT
            })

            if ((result.scoped ?? '') !== requested) {
              return []
            }

            return (result.work ?? []).map(item => ({ ...item, profile: name }))
          })
        )

        if (cancelled) {
          return
        }

        const next = sortRunsNewestFirst(batches.flat())

        setRuns(current => (JSON.stringify(current) === JSON.stringify(next) ? current : next))
        failures = 0
      } catch {
        failures++
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
  }, [enabled, profilesKey, requestGateway])

  return runs
}
