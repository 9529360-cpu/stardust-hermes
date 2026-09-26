import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getMemoryStatus, resetMemory } from '@/hermes'
import { confirm } from '@/store/confirm'
import { requestFreshSession } from '@/store/profile'

import { MaintenancePanel } from './maintenance'

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getActionStatus: vi.fn(() => Promise.resolve({ lines: [], running: false })),
  getCuratorStatus: vi.fn(() =>
    Promise.resolve({ enabled: false, last_run_at: null, paused: false })
  ),
  getMemoryStatus: vi.fn(() =>
    Promise.resolve({
      active: '',
      available: [],
      builtin_files: { memory: 12, user: 8 }
    })
  ),
  resetMemory: vi.fn(() =>
    Promise.resolve({
      active_session_behavior: 'refresh_on_next_turn',
      deleted: ['MEMORY.md'],
      ok: true,
      reset: ['MEMORY.md']
    })
  )
}))

vi.mock('@/store/confirm', () => ({ confirm: vi.fn() }))
vi.mock('@/store/profile', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  requestFreshSession: vi.fn()
}))

afterEach(cleanup)

describe('Command Center memory reset forget boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getMemoryStatus).mockResolvedValue({
      active: '',
      available: [],
      builtin_files: { memory: 12, user: 8 }
    })
  })

  it('explains stale chats before reset and offers a fresh session after success', async () => {
    vi.mocked(confirm).mockResolvedValueOnce(true).mockResolvedValueOnce(true)

    render(<MaintenancePanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset memory' }))

    await waitFor(() => expect(resetMemory).toHaveBeenCalledWith('memory'))
    expect(vi.mocked(confirm).mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({
        destructive: true,
        description: expect.stringContaining('reset fence')
      })
    )
    expect(vi.mocked(confirm).mock.calls[1]?.[0]).toEqual(
      expect.objectContaining({
        confirmLabel: 'Start new session',
        description: expect.stringContaining('fresh session')
      })
    )
    expect(requestFreshSession).toHaveBeenCalledTimes(1)
  })

  it('does not reset when the destructive confirmation is declined', async () => {
    vi.mocked(confirm).mockResolvedValueOnce(false)

    render(<MaintenancePanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset memory' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(resetMemory).not.toHaveBeenCalled()
    expect(requestFreshSession).not.toHaveBeenCalled()
  })

  it('keeps the current chat when the fresh-session offer is declined', async () => {
    vi.mocked(confirm).mockResolvedValueOnce(true).mockResolvedValueOnce(false)

    render(<MaintenancePanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset memory' }))

    await waitFor(() => expect(resetMemory).toHaveBeenCalledWith('memory'))
    expect(requestFreshSession).not.toHaveBeenCalled()
  })
})
