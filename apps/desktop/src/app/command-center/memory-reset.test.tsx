import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { deleteMemoryEntry, getMemoryEntries, getMemoryStatus, resetMemory } from '@/hermes'
import { confirm } from '@/store/confirm'
import { $activeGatewayProfile, requestFreshSession } from '@/store/profile'

import { MaintenancePanel } from './maintenance'

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getActionStatus: vi.fn(() => Promise.resolve({ lines: [], running: false })),
  getCuratorStatus: vi.fn(() => Promise.resolve({ enabled: false, last_run_at: null, paused: false })),
  getMemoryStatus: vi.fn(() =>
    Promise.resolve({
      active: '',
      providers: [],
      builtin_files: { memory: 12, user: 8 }
    })
  ),
  getMemoryEntries: vi.fn(() =>
    Promise.resolve({ available: true, entries: ['Likes tea', 'Lives in Berlin'], target: 'memory' })
  ),
  deleteMemoryEntry: vi.fn(() => Promise.resolve({ active_session_behavior: 'refresh_on_next_turn', ok: true })),
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
    $activeGatewayProfile.set('default')
    vi.mocked(getMemoryStatus).mockResolvedValue({
      active: '',
      providers: [],
      builtin_files: { memory: 12, user: 8 }
    })
  })

  it('shows individual entries and asks before removing exactly the selected one', async () => {
    vi.mocked(confirm).mockResolvedValueOnce(true).mockResolvedValueOnce(false)
    render(<MaintenancePanel />)

    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    expect(await screen.findByText('Likes tea')).toBeTruthy()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove entry' }))[0]!)

    await waitFor(() => expect(deleteMemoryEntry).toHaveBeenCalledWith('memory', 'Likes tea', 'default'))
    expect(vi.mocked(confirm).mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({
        destructive: true,
        description: 'Likes tea'
      })
    )
    expect(requestFreshSession).not.toHaveBeenCalled()
  })

  it('does not call the delete endpoint when entry confirmation is cancelled', async () => {
    vi.mocked(confirm).mockResolvedValueOnce(false)
    render(<MaintenancePanel />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove entry' }))[0]!)
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(deleteMemoryEntry).not.toHaveBeenCalled()
  })

  it('distinguishes a successfully loaded empty file', async () => {
    vi.mocked(getMemoryEntries).mockResolvedValueOnce({ available: true, entries: [], target: 'memory' })
    render(<MaintenancePanel />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    expect(await screen.findByText('No entries')).toBeTruthy()
  })

  it('distinguishes unavailable built-in memory from an empty file', async () => {
    vi.mocked(getMemoryEntries).mockResolvedValueOnce({ available: false, entries: [], target: 'memory' })
    render(<MaintenancePanel />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    expect(await screen.findByText('Built-in memory is unavailable for this profile.')).toBeTruthy()
    expect(screen.queryByText('No entries')).toBeNull()
  })

  it('does not delete the original profile entry after confirmation if the profile changed', async () => {
    let resolveConfirmation!: (confirmed: boolean) => void
    vi.mocked(confirm).mockImplementationOnce(() => new Promise(resolve => (resolveConfirmation = resolve)))
    render(<MaintenancePanel />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove entry' }))[0]!)
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))

    act(() => $activeGatewayProfile.set('research'))
    resolveConfirmation(true)
    await waitFor(() => expect(deleteMemoryEntry).not.toHaveBeenCalled())
    act(() => $activeGatewayProfile.set('default'))
  })

  it('suppresses stale delete completion after a profile switch', async () => {
    let resolveDelete!: (result: Awaited<ReturnType<typeof deleteMemoryEntry>>) => void
    vi.mocked(confirm).mockResolvedValueOnce(true)
    vi.mocked(deleteMemoryEntry).mockImplementationOnce(() => new Promise(resolve => (resolveDelete = resolve)))
    render(<MaintenancePanel />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove entry' }))[0]!)
    await waitFor(() => expect(deleteMemoryEntry).toHaveBeenCalledTimes(1))

    act(() => $activeGatewayProfile.set('research'))
    resolveDelete({ active_session_behavior: 'refresh_on_next_turn', deleted: ['MEMORY.md'], ok: true })
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(requestFreshSession).not.toHaveBeenCalled()
    act(() => $activeGatewayProfile.set('default'))
  })

  it('ignores a stale entry-read rejection across an A-to-B-to-A profile switch', async () => {
    let rejectFirstRead!: (error: Error) => void
    vi.mocked(getMemoryEntries)
      .mockImplementationOnce(
        () =>
          new Promise<Awaited<ReturnType<typeof getMemoryEntries>>>((_resolve, reject) => (rejectFirstRead = reject))
      )
      .mockResolvedValueOnce({ available: true, entries: ['Likes tea'], target: 'memory' })
    render(<MaintenancePanel />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    await waitFor(() => expect(getMemoryEntries).toHaveBeenCalledTimes(1))

    act(() => {
      $activeGatewayProfile.set('research')
      $activeGatewayProfile.set('default')
    })
    fireEvent.click((await screen.findAllByRole('button', { name: 'View entries' }))[0]!)
    expect(await screen.findByText('Likes tea')).toBeTruthy()
    rejectFirstRead(new Error('stale read'))
    await waitFor(() => expect(getMemoryEntries).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('Built-in memory is unavailable for this profile.')).toBeNull()
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

  it('does not report a committed reset as failed when the status refresh fails', async () => {
    vi.mocked(confirm).mockResolvedValueOnce(true).mockResolvedValueOnce(true)
    vi.mocked(getMemoryStatus)
      .mockResolvedValueOnce({
        active: '',
        providers: [],
        builtin_files: { memory: 12, user: 8 }
      })
      .mockRejectedValueOnce(new Error('status refresh unavailable'))

    render(<MaintenancePanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Reset memory' }))

    await waitFor(() => expect(resetMemory).toHaveBeenCalledWith('memory'))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2))
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
