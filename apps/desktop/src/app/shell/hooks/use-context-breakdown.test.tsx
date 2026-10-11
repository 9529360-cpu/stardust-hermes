// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { deferred } from '@/test/deferred'
import type { ContextBreakdown } from '@/types/hermes'

import { useContextBreakdown } from './use-context-breakdown'

interface Props {
  busy: boolean
  enabled: boolean
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  sessionId: null | string
}

describe('useContextBreakdown loading flag', () => {
  it('reads loading only while the breakdown is in flight', async () => {
    const read = deferred<ContextBreakdown>()
    const requestGateway = vi.fn(() => read.promise) as Props['requestGateway']

    const { result } = renderHook((props: Props) => useContextBreakdown(props), {
      initialProps: { busy: false, enabled: true, requestGateway, sessionId: 'session-a' }
    })

    expect(result.current.loading).toBe(true)

    await act(async () => {
      read.resolve({} as ContextBreakdown)
    })

    expect(result.current.loading).toBe(false)
  })

  it('does not leave the flag on when a draft supersedes a read in flight', () => {
    const read = deferred<ContextBreakdown>()
    const requestGateway = vi.fn(() => read.promise) as Props['requestGateway']

    const { rerender, result } = renderHook((props: Props) => useContextBreakdown(props), {
      initialProps: { busy: false, enabled: true, requestGateway, sessionId: 'session-a' as null | string }
    })

    expect(result.current.loading).toBe(true)

    // A new draft has no session to read, so nothing is in flight for it.
    rerender({ busy: false, enabled: true, requestGateway, sessionId: null })

    expect(result.current.loading).toBe(false)
  })
})
