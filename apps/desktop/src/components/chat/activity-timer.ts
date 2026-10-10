import { useEffect, useRef, useState } from 'react'

import { useViewedInterval } from '@/hooks/use-viewed-interval'

// Module-level registry so timers survive component unmount/remount (e.g.
// when a tool row scrolls out and back). Keyed by caller-supplied timerKey;
// anonymous timers (no key) start fresh each mount.
const startedAtByKey = new Map<string, number>()

function startedAt(key?: string): number {
  if (!key) {
    return Date.now()
  }

  const existing = startedAtByKey.get(key)

  if (existing !== undefined) {
    return existing
  }

  const now = Date.now()
  startedAtByKey.set(key, now)

  return now
}

const twoDigits = (value: number) => String(value).padStart(2, '0')

/** Clock-style elapsed time for live timers: `12s`, `1:05`, `1:02:05`. */
export function formatElapsed(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`
  }

  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}:${twoDigits(seconds % 60)}`
  }

  return `${Math.floor(seconds / 3600)}:${twoDigits(Math.floor((seconds % 3600) / 60))}:${twoDigits(seconds % 60)}`
}

/**
 * Seconds a reasoning block was open, from the timeline stamps the stream wrote
 * on it: `timestamp` when its first delta arrived, `completedAt` when the next
 * visible segment began or the turn settled. Null until both ends exist, and
 * when they disagree (mixed clocks), since a made-up duration is worse than
 * none. History rows carry no stamps, so a reloaded block has no duration.
 */
export function reasoningSeconds(timestamp: number | undefined, completedAt: number | undefined): null | number {
  const valid = (value: number | undefined): value is number =>
    typeof value === 'number' && Number.isFinite(value) && value > 0

  if (!valid(timestamp) || !valid(completedAt) || completedAt < timestamp) {
    return null
  }

  return completedAt - timestamp
}

/**
 * Seconds since the timer's origin, reported once a second while `active`.
 *
 * Origin, in order: an explicit `since` timestamp, else the `timerKey`'s
 * registry entry (survives unmount/remount), else mount time. Pass `since` when
 * the thing being measured started at a moment the caller knows and that moment
 * isn't the mount — otherwise an anonymous timer reports the component's age,
 * which is only the same number by accident.
 */
export function useElapsedSeconds(active = true, timerKey?: string, since?: number): number {
  const start = useRef(since ?? startedAt(timerKey))
  const lastKey = useRef(timerKey)
  const [elapsed, setElapsed] = useState(() => Math.max(0, Math.floor((Date.now() - start.current) / 1000)))

  if (lastKey.current !== timerKey) {
    start.current = since ?? startedAt(timerKey)
    lastKey.current = timerKey
  }

  // eslint-disable-next-line no-restricted-syntax -- timer origin is imperative state, not an atom mirror
  useEffect(() => {
    if (since !== undefined) {
      start.current = since
    } else if (timerKey) {
      start.current = startedAt(timerKey)
    }

    if (active) {
      setElapsed(Math.max(0, Math.floor((Date.now() - start.current) / 1000)))
    }
  }, [active, since, timerKey])

  useViewedInterval(() => setElapsed(Math.max(0, Math.floor((Date.now() - start.current) / 1000))), 1000, active)

  return elapsed
}

export function __resetElapsedTimerRegistryForTests() {
  startedAtByKey.clear()
}
