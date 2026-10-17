import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const { prewarmProfileBackend } = vi.hoisted(() => ({ prewarmProfileBackend: vi.fn() }))

vi.mock('@/store/profile', () => ({ prewarmProfileBackend }))

import { useProfilePrewarm } from './profile-prewarm'

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('useProfilePrewarm', () => {
  it('waits for hover dwell and warms the current source-qualified profile', () => {
    vi.useFakeTimers()

    const initialProps: { connectionId: null | string; profile: string } = { connectionId: null, profile: 'alpha' }

    const { result, rerender } = renderHook(
      ({ connectionId, profile }: { connectionId: null | string; profile: string }) =>
        useProfilePrewarm(profile, connectionId),
      { initialProps }
    )

    act(() => result.current.startPrewarm())
    act(() => vi.advanceTimersByTime(119))
    expect(prewarmProfileBackend).not.toHaveBeenCalled()

    rerender({ connectionId: 'work', profile: 'research' })
    act(() => vi.advanceTimersByTime(1))

    expect(prewarmProfileBackend).toHaveBeenCalledWith('research', 'work')
  })

  it('cancels pending warm-up when the pointer leaves or the row unmounts', () => {
    vi.useFakeTimers()

    const { result, unmount } = renderHook(() => useProfilePrewarm('beta', 'local'))

    act(() => result.current.startPrewarm())
    act(() => result.current.cancelPrewarm())
    act(() => vi.advanceTimersByTime(120))
    expect(prewarmProfileBackend).not.toHaveBeenCalled()

    act(() => result.current.startPrewarm())
    unmount()
    act(() => vi.advanceTimersByTime(120))
    expect(prewarmProfileBackend).not.toHaveBeenCalled()
  })
})
