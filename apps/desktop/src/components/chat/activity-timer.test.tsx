import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetElapsedTimerRegistryForTests,
  formatElapsed,
  reasoningSeconds,
  useElapsedSeconds
} from './activity-timer'

function Probe({ active, since, timerKey }: { active: boolean; since?: number; timerKey?: string }) {
  const elapsed = useElapsedSeconds(active, timerKey, since)

  return <span data-testid="elapsed">{elapsed}</span>
}

describe('useElapsedSeconds', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00.000Z'))
    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    __resetElapsedTimerRegistryForTests()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    __resetElapsedTimerRegistryForTests()
  })

  it('keeps elapsed time stable across remounts for the same key', () => {
    const first = render(<Probe active timerKey="tool:abc" />)

    act(() => {
      vi.advanceTimersByTime(5_000)
    })

    expect(screen.getByTestId('elapsed').textContent).toBe('5')

    first.unmount()

    act(() => {
      vi.advanceTimersByTime(3_000)
    })

    render(<Probe active timerKey="tool:abc" />)

    expect(screen.getByTestId('elapsed').textContent).toBe('8')
  })

  it('counts from an explicit epoch rather than mount time', () => {
    const mountedAt = Date.now()

    act(() => {
      vi.advanceTimersByTime(30_000)
    })

    render(<Probe active since={mountedAt + 28_000} />)

    expect(screen.getByTestId('elapsed').textContent).toBe('2')
  })

  it('re-anchors when the epoch moves', () => {
    const { rerender } = render(<Probe active since={Date.now()} />)

    act(() => {
      vi.advanceTimersByTime(10_000)
    })

    expect(screen.getByTestId('elapsed').textContent).toBe('10')

    rerender(<Probe active since={Date.now()} />)

    expect(screen.getByTestId('elapsed').textContent).toBe('0')
  })

  it('pauses UI ticks without focus and catches up immediately on return', () => {
    render(<Probe active timerKey="tool:background" />)
    vi.mocked(document.hasFocus).mockReturnValue(false)
    window.dispatchEvent(new Event('blur'))

    act(() => {
      vi.advanceTimersByTime(5_000)
    })
    expect(screen.getByTestId('elapsed').textContent).toBe('0')

    vi.mocked(document.hasFocus).mockReturnValue(true)
    act(() => window.dispatchEvent(new Event('focus')))
    expect(screen.getByTestId('elapsed').textContent).toBe('5')
  })
})

describe('formatElapsed', () => {
  it('reads seconds, then minutes and seconds, then hours and minutes', () => {
    expect(formatElapsed(0)).toBe('0s')
    expect(formatElapsed(59)).toBe('59s')
    expect(formatElapsed(65)).toBe('1:05')
    expect(formatElapsed(3_599)).toBe('59:59')
    expect(formatElapsed(3_600)).toBe('1:00:00')
    expect(formatElapsed(3_725)).toBe('1:02:05')
  })
})

describe('reasoningSeconds', () => {
  it('is the span from the first delta to the segment that ended the block', () => {
    expect(reasoningSeconds(1_000.25, 1_012.75)).toBeCloseTo(12.5)
  })

  it('has no span until both ends are stamped', () => {
    expect(reasoningSeconds(undefined, 1_012)).toBeNull()
    expect(reasoningSeconds(1_000, undefined)).toBeNull()
  })

  it('refuses a span whose ends disagree rather than inventing one', () => {
    expect(reasoningSeconds(1_012, 1_000)).toBeNull()
  })

  it('ignores stamps that are not real unix times', () => {
    expect(reasoningSeconds(0, 1_012)).toBeNull()
    expect(reasoningSeconds(Number.NaN, 1_012)).toBeNull()
  })
})
