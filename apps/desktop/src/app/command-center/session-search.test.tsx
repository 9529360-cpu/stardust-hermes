import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as HermesApi from '@/hermes'
import type { SessionInfo, SessionSearchResponse } from '@/hermes'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection, $sessions } from '@/store/session'

import { CommandCenterView } from './index'

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

const LOADED = {
  ended_at: null,
  id: 'loaded-1',
  input_tokens: 0,
  is_active: false,
  last_active: 1_756_600_000,
  message_count: 3,
  model: null,
  output_tokens: 0,
  preview: 'A preview that does not contain the body match',
  source: 'desktop',
  started_at: 1_756_500_000,
  title: 'Weekly groceries',
  tool_call_count: 0
} satisfies SessionInfo

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
        source: 'telegram',
        title: 'Older conversation'
      }
    ]
  }
}

function renderCommandCenter(onOpenSession = vi.fn()) {
  const view = render(
    <MemoryRouter>
      <CommandCenterView
        initialSection="sessions"
        onClose={() => {}}
        onDeleteSession={() => Promise.resolve()}
        onOpenSession={onOpenSession}
      />
    </MemoryRouter>
  )

  return { ...view, onOpenSession }
}

const searchBox = () => screen.getByRole('textbox')
const row = (id: string) => document.querySelector(`[data-session-search-result="${id}"]`)
const snippet = (id: string) => row(id)?.querySelector('[data-session-search-snippet]')
const changeQuery = (value: string) => fireEvent.change(searchBox(), { target: { value } })

function deferredSearch() {
  const pending = new Map<string, (response: SessionSearchResponse) => void>()
  searchSessions.mockImplementation(query => new Promise(resolve => pending.set(query, resolve)))

  return pending
}

async function resolveHit(pending: ReturnType<typeof deferredSearch>, query: string, id: string) {
  await act(async () => {
    pending.get(query)!(hit(id, `the ${query} answer`))
  })
}

describe('Command Center session search', () => {
  beforeEach(() => {
    $sessions.set([LOADED])
    $connection.set(null)
    $activeGatewayProfile.set('default')
    searchSessions.mockReset()
  })

  afterEach(() => {
    cleanup()
    $sessions.set([])
    $connection.set(null)
    $activeGatewayProfile.set('default')
  })

  it('focuses search and opens a full-text result outside the loaded page using its real title and id', async () => {
    searchSessions.mockResolvedValue(hit('older-telegram', 'we chose the >>>harbor<<< route'))
    const { onOpenSession } = renderCommandCenter()
    expect(document.activeElement).toBe(searchBox())
    changeQuery('harbor')

    await waitFor(() => expect(snippet('older-telegram')?.textContent).toBe('we chose the harbor route'))
    expect(searchSessions).toHaveBeenLastCalledWith('harbor')
    expect(row('loaded-1')).toBeNull()
    expect(row('older-telegram')?.textContent).toContain('Older conversation')
    expect(row('older-telegram')?.textContent).toContain('Telegram')
    expect(snippet('older-telegram')?.querySelector('mark')?.textContent).toBe('harbor')
    fireEvent.click(row('older-telegram')!.querySelector('button')!)
    expect(onOpenSession).toHaveBeenCalledWith(
      'older-telegram',
      expect.objectContaining({ id: 'older-telegram', profile: 'default' })
    )
  })

  it('opens a remote backend default result through the requesting desktop alias', async () => {
    $activeGatewayProfile.set('mara')
    const response = hit('remote-old', 'remote alias answer')
    response.results[0].profile = 'default'
    searchSessions.mockResolvedValue(response)
    const { onOpenSession } = renderCommandCenter()
    changeQuery('alias')
    await waitFor(() => expect(row('remote-old')).not.toBeNull())
    fireEvent.click(row('remote-old')!.querySelector('button')!)
    expect(onOpenSession).toHaveBeenCalledWith(
      'remote-old',
      expect.objectContaining({ profile: 'mara' })
    )
  })

  it('shows the actual body hit for a loaded lineage while keeping its title and one open action', async () => {
    $sessions.set([{ ...LOADED, _lineage_root_id: 'root' }])
    const response = hit('compressed-old', 'remember the >>>harbor<<< plan')
    response.results[0].lineage_root = 'root'
    searchSessions.mockResolvedValue(response)
    renderCommandCenter()
    changeQuery('harbor')

    await waitFor(() => expect(snippet('loaded-1')?.textContent).toBe('remember the harbor plan'))
    expect(row('loaded-1')?.textContent).toContain('Weekly groceries')
    expect(document.querySelectorAll('[data-session-search-result]')).toHaveLength(1)
    expect(row('loaded-1')?.textContent).not.toContain(LOADED.preview)
  })

  it('shows local matches immediately, reports incomplete full-text search, and retries the same query', async () => {
    searchSessions.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(hit('older', 'grocer details'))
    renderCommandCenter()
    changeQuery('grocer')
    expect(row('loaded-1')).not.toBeNull()
    await screen.findByRole('alert')
    expect(row('loaded-1')).not.toBeNull()
    expect(screen.queryByText('No matching results found.')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Retry|重试/ }))
    await waitFor(() => expect(row('older')).not.toBeNull())
    expect(searchSessions).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('hides old results as soon as the query changes, even before debounce finishes', async () => {
    searchSessions.mockResolvedValue(hit('alpha-session', 'the alpha answer'))
    renderCommandCenter()
    changeQuery('alpha')
    await waitFor(() => expect(row('alpha-session')).not.toBeNull())
    changeQuery('beta')
    expect(row('alpha-session')).toBeNull()
  })

  it('never lets a slower response replace the current query results', async () => {
    const pending = deferredSearch()
    renderCommandCenter()
    changeQuery('alpha')
    await waitFor(() => expect(pending.has('alpha')).toBe(true))
    changeQuery('beta')
    await waitFor(() => expect(pending.has('beta')).toBe(true))
    await resolveHit(pending, 'beta', 'beta-session')
    await resolveHit(pending, 'alpha', 'alpha-session')
    expect(row('alpha-session')).toBeNull()
    expect(row('beta-session')).not.toBeNull()
  })

  it('clearing the query restores loaded rows and rejects a late response', async () => {
    const pending = deferredSearch()
    renderCommandCenter()
    changeQuery('alpha')
    await waitFor(() => expect(pending.has('alpha')).toBe(true))
    changeQuery('')
    expect(row('loaded-1')).not.toBeNull()
    await resolveHit(pending, 'alpha', 'alpha-session')
    expect(row('alpha-session')).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('leaving the sessions panel cancels publication and re-entering searches afresh', async () => {
    const pending = deferredSearch()
    renderCommandCenter()
    changeQuery('alpha')
    await waitFor(() => expect(pending.has('alpha')).toBe(true))
    fireEvent.click(screen.getByRole('button', { name: /System|系统/ }))
    await resolveHit(pending, 'alpha', 'alpha-session')
    fireEvent.click(screen.getByRole('button', { name: /^Chats$|^对话$/ }))
    expect(row('alpha-session')).toBeNull()
    await waitFor(() => expect(searchSessions).toHaveBeenCalledTimes(2))
  })

  it('isolates results and loaded local matches when the active profile changes', async () => {
    const pending = deferredSearch()
    renderCommandCenter()
    changeQuery('grocer')
    await waitFor(() => expect(pending.has('grocer')).toBe(true))
    await act(async () => {
      $activeGatewayProfile.set('work')
    })
    expect(row('loaded-1')).toBeNull()
    await resolveHit(pending, 'grocer', 'old-profile')
    expect(row('old-profile')).toBeNull()
    await waitFor(() => expect(searchSessions).toHaveBeenCalledTimes(2))
  })

  it('isolates the same query across connection switches', async () => {
    const pending = deferredSearch()
    renderCommandCenter()
    changeQuery('alpha')
    await waitFor(() => expect(pending.has('alpha')).toBe(true))
    await act(async () => {
      $connection.set({ connectionId: 'remote', mode: 'remote', baseUrl: 'http://example.test' } as never)
    })
    await resolveHit(pending, 'alpha', 'old-connection')
    expect(row('old-connection')).toBeNull()
    await waitFor(() => expect(searchSessions).toHaveBeenCalledTimes(2))
  })

  it('renders HTML-looking snippet text safely and highlights a literal Chinese query', async () => {
    searchSessions.mockResolvedValue(hit('html', '<img src=x onerror=alert(1)> 暗号星尘 <script>bad()</script>'))
    renderCommandCenter()
    changeQuery('暗号星尘')
    await waitFor(() => expect(snippet('html')?.textContent).toContain('<img src=x onerror=alert(1)>'))
    expect(snippet('html')?.querySelector('img, script')).toBeNull()
    expect(snippet('html')?.querySelector('mark')?.textContent).toBe('暗号星尘')
  })
})
