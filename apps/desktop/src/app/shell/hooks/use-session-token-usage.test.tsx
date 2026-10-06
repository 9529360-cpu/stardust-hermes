import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useSessionTokenUsage } from './use-session-token-usage'

afterEach(() => vi.restoreAllMocks())

describe('useSessionTokenUsage', () => {
  it('fetches only while enabled, and refreshes after a busy turn', async () => {
    const requestGateway = vi.fn().mockResolvedValue({ calls: 1, input: 10, output: 2, total: 12 })
    const { rerender, result } = renderHook(
      ({ busy, enabled }) => useSessionTokenUsage({ busy, enabled, requestGateway, sessionId: 'runtime-1' }),
      { initialProps: { busy: false, enabled: false } }
    )

    expect(requestGateway).not.toHaveBeenCalled()
    rerender({ busy: false, enabled: true })
    await waitFor(() => expect(result.current?.total).toBe(12))
    expect(requestGateway).toHaveBeenCalledWith('session.usage', { session_id: 'runtime-1' })

    rerender({ busy: true, enabled: true })
    expect(requestGateway).toHaveBeenCalledTimes(1)
    rerender({ busy: false, enabled: true })
    await waitFor(() => expect(requestGateway).toHaveBeenCalledTimes(2))
  })

  it('does not show stale counts while refreshing a completed turn', async () => {
    let resolveRefresh!: (value: { calls: number; input: number; output: number; total: number }) => void
    const requestGateway = vi.fn()
      .mockResolvedValueOnce({ calls: 1, input: 10, output: 2, total: 12 })
      .mockImplementationOnce(() => new Promise(resolve => { resolveRefresh = resolve }))
    const { rerender, result } = renderHook(
      ({ busy }) => useSessionTokenUsage({ busy, enabled: true, requestGateway, sessionId: 'runtime-1' }),
      { initialProps: { busy: false } }
    )
    await waitFor(() => expect(result.current?.total).toBe(12))
    rerender({ busy: true })
    rerender({ busy: false })
    expect(result.current).toBeNull()
    await act(async () => {
      resolveRefresh({ calls: 2, input: 20, output: 3, total: 23 })
    })
    expect(result.current?.total).toBe(23)
  })

  it('does not restore stale counts if the idle refresh fails', async () => {
    let rejectRefresh!: (reason: Error) => void
    const requestGateway = vi.fn()
      .mockResolvedValueOnce({ calls: 1, input: 10, output: 2, total: 12 })
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRefresh = reject }))
    const { rerender, result } = renderHook(
      ({ busy }) => useSessionTokenUsage({ busy, enabled: true, requestGateway, sessionId: 'runtime-1' }),
      { initialProps: { busy: false } }
    )
    await waitFor(() => expect(result.current?.total).toBe(12))
    rerender({ busy: true })
    rerender({ busy: false })
    expect(result.current).toBeNull()
    await act(async () => { rejectRefresh(new Error('offline')) })
    expect(result.current).toBeNull()
  })

  it('drops a prior session immediately and ignores a late response after switching', async () => {
    let resolveFirst!: (value: { calls: number; input: number; output: number; total: number }) => void
    const requestGateway = vi.fn()
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
      .mockResolvedValue({ calls: 2, input: 20, output: 3, total: 23 })
    const { rerender, result } = renderHook(
      ({ sessionId }) => useSessionTokenUsage({ busy: false, enabled: true, requestGateway, sessionId }),
      { initialProps: { sessionId: 'runtime-1' } }
    )

    rerender({ sessionId: 'runtime-2' })
    expect(result.current).toBeNull()
    await waitFor(() => expect(result.current?.total).toBe(23))
    resolveFirst({ calls: 1, input: 10, output: 2, total: 12 })
    await waitFor(() => expect(result.current?.total).toBe(23))
    expect(requestGateway).toHaveBeenLastCalledWith('session.usage', { session_id: 'runtime-2' })
  })
})
