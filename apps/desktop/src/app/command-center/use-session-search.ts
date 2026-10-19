import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

import { searchSessions } from '@/hermes'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'
import type { SessionSearchResult } from '@/types/hermes'

const EMPTY_RESULTS: SessionSearchResult[] = []

type SearchState = {
  key: string
  results: SessionSearchResult[]
  status: 'pending' | 'success' | 'error'
}

function currentScopeKey(): string {
  const connection = $connection.get()

  return JSON.stringify([
    connection?.connectionId,
    connection?.mode,
    connection?.baseUrl,
    connection?.profile,
    $activeGatewayProfile.get()
  ])
}

/** Ephemeral full-text results belong to one query and one backend/profile.
 * The bridge has no abort capability: cleanup cancels publication, including
 * during debounce, rather than pretending it cancels the underlying request. */
export function useSessionSearch(query: string, enabled: boolean) {
  const connection = useStore($connection)
  const profile = useStore($activeGatewayProfile)

  const scope = JSON.stringify([
    connection?.connectionId,
    connection?.mode,
    connection?.baseUrl,
    connection?.profile,
    profile
  ])

  const trimmedQuery = query.trim()
  const key = JSON.stringify([scope, trimmedQuery])
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<SearchState | null>(null)
  const retry = useCallback(() => setAttempt(value => value + 1), [])

  useEffect(() => {
    if (!enabled || !trimmedQuery) {
      return
    }

    let cancelled = false

    setState({ key, results: EMPTY_RESULTS, status: 'pending' })

    const timer = window.setTimeout(() => {
      void searchSessions(trimmedQuery).then(
        response => {
          if (!cancelled && currentScopeKey() === scope) {
            setState({
              key,
              results: response.results.map(result => ({
                ...result,
                // Backend profile names may differ from the desktop routing alias.
                profile,
                connection_id: $connection.get()?.connectionId
              })),
              status: 'success'
            })
          }
        },
        () => {
          if (!cancelled && currentScopeKey() === scope) {
            setState({ key, results: EMPTY_RESULTS, status: 'error' })
          }
        }
      )
    }, 180)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [attempt, enabled, key, profile, scope, trimmedQuery])

  const active = enabled && Boolean(trimmedQuery)
  const current = active && state?.key === key ? state : null

  const matchesScope = useCallback(
    (session: { connection_id?: string; profile?: string }) =>
      (session.profile?.trim() || 'default') === (profile.trim() || 'default') &&
      (session.connection_id
        ? session.connection_id === (connection?.connectionId ?? 'local')
        : !connection?.connectionId || connection.connectionId === 'local'),
    [connection?.connectionId, profile]
  )

  return {
    query: trimmedQuery,
    results: current?.results ?? EMPTY_RESULTS,
    pending: active && (!current || current.status === 'pending'),
    failed: current?.status === 'error',
    retry,
    matchesScope
  }
}
