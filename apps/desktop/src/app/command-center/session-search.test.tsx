import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as HermesApi from '@/hermes'
import type { SessionInfo, SessionSearchResponse } from '@/hermes'
import { $sessions } from '@/store/session'

import { CommandCenterView } from './index'

// The sidebar's persistent search field is gone; `session.focusSearch` now
// lands here. This panel must keep the full-text reach that field had: hits
// from the backend's message index, including conversations outside the
// loaded page, not just title matches over what happens to be loaded.

const searchSessions = vi.hoisted(() => vi.fn<(query: string) => Promise<SessionSearchResponse>>())

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof HermesApi>()),
  getActionStatus: vi.fn(() => Promise.resolve({ running: false })),
  getLogs: vi.fn(() => Promise.resolve({ lines: [] })),
  getStatus: vi.fn(() => Promise.resolve({})),
  getUsageAnalytics: vi.fn(() => Promise.resolve({})),
  restartGateway: vi.fn(),
  searchSessions,
  updateHermes: vi.fn()
}))
vi.mock('@/lib/session-export', () => ({ exportSession: vi.fn() }))
vi.mock('./maintenance', () => ({ MaintenancePanel: () => null }))

const LOADED: SessionInfo = {
  ended_at: null,
  id: 'loaded-1',
  input_tokens: 0,
  is_active: false,
  last_active: 1_756_600_000,
  message_count: 3,
  model: null,
  output_tokens: 0,
  started_at: 1_756_500_000,
  title: 'Weekly groceries'
} as SessionInfo

function hit(sessionId: string, snippet: string): SessionSearchResponse {
  return {
    results: [
      {
        lineage_root: null,
        model: null,
        role: 'assistant',
        session_id: sessionId,
        session_started: 1_750_000_000,
        snippet,
        source: 'telegram'
      }
    ]
  }
}

function renderCommandCenter(onOpenSession = vi.fn()) {
  render(
    <MemoryRouter>
      <CommandCenterView
        initialSection="sessions"
        onClose={() => {}}
        onDeleteSession={() => Promise.resolve()}
        onOpenSession={onOpenSession}
      />
    </MemoryRouter>
  )

  return onOpenSession
}

const searchBox = () => screen.getByRole('textbox')

describe('Command Center session search', () => {
  beforeEach(() => {
    $sessions.set([LOADED])
    searchSessions.mockReset()
  })

  afterEach(() => {
    cleanup()
    $sessions.set([])
  })

  it('finds a conversation outside the loaded page through the full-text index and opens it by id', async () => {
    searchSessions.mockResolvedValue(hit('older-telegram', 'we chose the >>>harbor<<< route'))
    const onOpenSession = renderCommandCenter()

    fireEvent.change(searchBox(), { target: { value: 'harbor' } })

    const row = await screen.findByText('we chose the harbor route')

    expect(searchSessions).toHaveBeenLastCalledWith('harbor')
    expect(screen.queryByText('Weekly groceries')).toBeNull()

    fireEvent.click(row)
    expect(onOpenSession).toHaveBeenCalledWith('older-telegram')
  })

  it('never lets a slower response for an older query replace the current results', async () => {
    const pending = new Map<string, (response: SessionSearchResponse) => void>()

    searchSessions.mockImplementation(query => new Promise(resolve => pending.set(query, resolve)))
    renderCommandCenter()

    fireEvent.change(searchBox(), { target: { value: 'alpha' } })
    await waitFor(() => expect(pending.has('alpha')).toBe(true))

    fireEvent.change(searchBox(), { target: { value: 'beta' } })
    await waitFor(() => expect(pending.has('beta')).toBe(true))

    pending.get('beta')!(hit('beta-session', 'the beta answer'))
    await screen.findByText('the beta answer')

    pending.get('alpha')!(hit('alpha-session', 'the alpha answer'))
    await new Promise(resolve => setTimeout(resolve, 50))

    expect(screen.queryByText('the alpha answer')).toBeNull()
    expect(screen.getByText('the beta answer')).toBeTruthy()
  })

  it('keeps instant client-side matches when the full-text index is unavailable', async () => {
    searchSessions.mockRejectedValue(new Error('offline'))
    renderCommandCenter()

    fireEvent.change(searchBox(), { target: { value: 'grocer' } })

    await waitFor(() => expect(searchSessions).toHaveBeenCalledWith('grocer'))
    expect(await screen.findByText('Weekly groceries')).toBeTruthy()
  })
})
