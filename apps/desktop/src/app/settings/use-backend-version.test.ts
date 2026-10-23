import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getStatus } from '@/hermes'

import { useBackendVersion } from './use-backend-version'

vi.mock('@/hermes', () => ({ getStatus: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('About backend version', () => {
  it('reads the connected backend and clears its version when the backend goes down', async () => {
    vi.mocked(getStatus).mockResolvedValue({ version: '2.3.4' } as never)

    const { result, rerender } = renderHook(({ state }) => useBackendVersion('local', state), {
      initialProps: { state: 'open' }
    })

    await waitFor(() => expect(result.current).toBe('2.3.4'))
    vi.mocked(getStatus).mockRejectedValue(new Error('unreachable'))
    rerender({ state: 'closed' })
    await waitFor(() => expect(result.current).toBeNull())
  })

  it('does not show a previous connection version or accept its late response after switching', async () => {
    let resolveOld!: (value: never) => void

    vi.mocked(getStatus).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))

    const { result, rerender } = renderHook(({ scope }) => useBackendVersion(scope, 'open'), {
      initialProps: { scope: 'old' }
    })

    vi.mocked(getStatus).mockResolvedValue({ version: '9.8.7' } as never)
    rerender({ scope: 'remote' })
    await waitFor(() => expect(result.current).toBe('9.8.7'))
    await act(async () => resolveOld({ version: '1.0.0' } as never))
    expect(result.current).toBe('9.8.7')
  })
})
