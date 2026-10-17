import { useCallback, useEffect, useRef } from 'react'

import { prewarmProfileBackend } from '@/store/profile'

// Dwell before firing: long enough that sweeping the pointer across the rail
// or a mixed-profile session list doesn't spawn a backend for every element
// passed through, short enough to beat the click by hundreds of ms.
const PREWARM_DWELL_MS = 120

/**
 * Pointerenter/pointerleave handlers that pre-warm a profile's pool backend
 * after a short hover dwell. A source ID keeps multi-source rows on their own
 * connection; consumers cancel on pointerdown when the user intends to click.
 */
export function useProfilePrewarm(profile: string | null | undefined, connectionId: null | string = null) {
  const timer = useRef<null | number>(null)
  const profileRef = useRef(profile)
  const connectionIdRef = useRef(connectionId)
  profileRef.current = profile
  connectionIdRef.current = connectionId

  const cancelPrewarm = useCallback(() => {
    if (timer.current != null) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  useEffect(() => cancelPrewarm, [cancelPrewarm])

  const startPrewarm = useCallback(() => {
    cancelPrewarm()
    timer.current = window.setTimeout(() => {
      timer.current = null
      prewarmProfileBackend(profileRef.current || 'default', connectionIdRef.current)
    }, PREWARM_DWELL_MS)
  }, [cancelPrewarm])

  return { cancelPrewarm, startPrewarm }
}
