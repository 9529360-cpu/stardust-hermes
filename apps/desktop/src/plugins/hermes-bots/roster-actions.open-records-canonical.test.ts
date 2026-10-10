/**
 * Opening a bot from the roster teaches the roster snapshot which Bot Chat that
 * bot owns.
 *
 * The snapshot refreshes only while the roster page is mounted, and the click
 * takes the page away. Without this write the bot's chat, created after the
 * last fetch, reads as chatless to the empty-chat state, the /new guard and the
 * Routines owner. A failed open must leave the snapshot as it was.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RosterRow } from './types'

const { hostMock, openBotCanonicalChatMock, notifyBotOpenFailureMock } = vi.hoisted(() => ({
  hostMock: {
    notify: vi.fn(),
    request: vi.fn(),
    setWorkspaceScope: vi.fn(),
    state: {
      connectionId: { get: () => 'local' },
      focusedSessionOwner: null,
      profile: { get: () => 'default' }
    }
  },
  notifyBotOpenFailureMock: vi.fn(),
  openBotCanonicalChatMock: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', async () => {
  const { atom } = await import('nanostores')

  return {
    ackStoredSessionId: vi.fn(),
    atom,
    haptic: vi.fn(),
    host: hostMock,
    markSessionUnreadFinished: vi.fn(),
    queryClient: { invalidateQueries: vi.fn() },
    useQuery: vi.fn(),
    useValue: vi.fn()
  }
})

// One open in flight, and it is the current one, so no open is superseded.
vi.mock('./shared', () => ({
  bumpBotOpenGeneration: () => 1,
  getBotOpenGeneration: () => 1,
  getPluginCtx: () => ({ storage: { get: vi.fn(), set: vi.fn() } }),
  ID: 'hermes-bots'
}))

vi.mock('./canonical-chat', () => ({
  CANONICAL_CHAT_TITLE: 'Bot Chat',
  notifyBotOpenFailure: notifyBotOpenFailureMock,
  openBotCanonicalChat: openBotCanonicalChatMock,
  prepareBotSource: vi.fn(async () => undefined)
}))

vi.mock('./group-chat', async () => {
  const { atom } = await import('nanostores')

  return { $groupChats: atom({}), $groupChatWorkspace: atom<null | string>(null) }
})
vi.mock('./group-chat-view', () => ({ openGroupChat: vi.fn() }))
vi.mock('./group-membership', () => ({ liveGroupChatNames: () => [] }))
vi.mock('./group-panes', () => ({ closeGroupChatMainTab: vi.fn() }))

const alpha = (): RosterRow => ({ canonical_session: null, connectionId: 'local', name: 'alpha' })

beforeEach(() => {
  vi.clearAllMocks()
})

describe('opening a bot from the roster', () => {
  it('names the Bot Chat it resolved on that bot in the roster snapshot', async () => {
    const { openRosterBot } = await import('./roster-actions')
    const { $lastRoster } = await import('./data')
    $lastRoster.set([alpha()])
    openBotCanonicalChatMock.mockResolvedValue({ openedId: 'tip-1', registryId: 'reg-1' })

    await expect(openRosterBot(alpha())).resolves.toBe(true)

    expect($lastRoster.get()[0].canonical_session).toEqual({ id: 'reg-1', resolved_id: 'tip-1' })
  })

  it('leaves the snapshot untouched when the open fails', async () => {
    const { openRosterBot } = await import('./roster-actions')
    const { $lastRoster } = await import('./data')
    const rows = [alpha()]
    $lastRoster.set(rows)
    openBotCanonicalChatMock.mockRejectedValue(new Error('gateway offline'))

    await expect(openRosterBot(alpha())).resolves.toBe(false)

    expect($lastRoster.get()).toBe(rows)
    expect(notifyBotOpenFailureMock).toHaveBeenCalledTimes(1)
  })
})
