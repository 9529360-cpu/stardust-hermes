import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRecentCronRuns } from './use-recent-cron-runs'

const requestGateway = vi.hoisted(() => vi.fn())

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

const RUN = {
  id: 'cron:run-1',
  kind: 'cron',
  title: 'Cron job job-1',
  status: 'completed',
  started_at: 1_790_000_000,
  updated_at: 1_790_000_060,
  detail: { job_id: 'job-1' }
}

describe('useRecentCronRuns', () => {
  beforeEach(() => {
    requestGateway.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reads the launch profile as an empty name and tags each run with the profile it came from', async () => {
    requestGateway.mockResolvedValue({ scoped: '', work: [RUN] })

    const { result } = renderHook(() => useRecentCronRuns(['default'], true))

    await waitFor(() => expect(result.current).toHaveLength(1))
    expect(requestGateway).toHaveBeenCalledWith('cron.executions.list', { profile: '', limit: 30 })
    expect(result.current[0]).toMatchObject({ id: RUN.id, profile: 'default' })
  })

  it('drops a response scoped to another profile instead of showing its runs under this one', async () => {
    requestGateway.mockResolvedValue({ scoped: 'coder', work: [RUN] })

    const { result } = renderHook(() => useRecentCronRuns(['research'], true))

    await waitFor(() => expect(requestGateway).toHaveBeenCalledTimes(1))
    expect(result.current).toEqual([])
  })

  it('reads nothing while disabled', () => {
    renderHook(() => useRecentCronRuns(['default'], false))

    expect(requestGateway).not.toHaveBeenCalled()
  })
})
