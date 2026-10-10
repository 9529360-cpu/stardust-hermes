// @vitest-environment jsdom
/**
 * The empty state a bot's chat shows before it has spoken.
 *
 * It identifies the bot by the registry row the roster snapshot names (the
 * identity the open path records), greets, and offers starter prompts. A
 * starter only fills the composer; nothing is sent until the user does it.
 * A chat that is not that bot's Bot Chat gets no state at all.
 */

import type * as HermesSdk from '@hermes/plugin-sdk'
import { cleanup, fireEvent, render, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BotChatEmpty } from './chat-empty'
import { $lastRoster } from './data'
import { translateBots } from './i18n-test-helper'
import type { RosterRow } from './types'

const { fillComposer } = vi.hoisted(() => ({ fillComposer: vi.fn() }))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()

  return {
    ...sdk,
    host: { ...sdk.host, fillComposer },
    // The plugin bundle lands via ctx.i18n.register at load; tests resolve the
    // English bundle directly instead.
    usePluginI18n: () => translateBots
  }
})

// The face is a canvas-animated component; the empty state only needs its slot.
vi.mock('./avatar', () => ({
  avatarColor: () => '#8b5cf6',
  botAppearance: () => ({ color: '#8b5cf6', image: null, shape: 'squircle' }),
  BotFace: () => null
}))

const alpha = (canonical: RosterRow['canonical_session']): RosterRow => ({
  canonical_session: canonical,
  connectionId: 'local',
  name: 'alpha'
})

const STARTERS = [
  translateBots('bot.starterIntro'),
  translateBots('bot.starterPlan'),
  translateBots('bot.starterSteps')
]

beforeEach(() => {
  fillComposer.mockClear()
})

afterEach(() => {
  cleanup()
  $lastRoster.set([])
})

describe('BotChatEmpty', () => {
  it('stands down for a chat that is not this bot’s Bot Chat', () => {
    $lastRoster.set([alpha({ id: 'bot-chat-1', resolved_id: 'bot-chat-1' })])

    const { container } = render(<BotChatEmpty sessionId="some-side-chat" />)

    expect(container.querySelector('[data-slot="bot_chat_empty"]')).toBeNull()
  })

  it('stands down until the roster names the chat, the stale-snapshot case', () => {
    $lastRoster.set([alpha(null)])

    const { container } = render(<BotChatEmpty sessionId="bot-chat-1" />)

    expect(container.querySelector('[data-slot="bot_chat_empty"]')).toBeNull()
  })

  it('greets the bot whose Bot Chat is on screen, matched by registry row or lineage tip', () => {
    $lastRoster.set([alpha({ id: 'bot-chat-1', resolved_id: 'bot-tip-2' })])

    const { container, rerender } = render(<BotChatEmpty sessionId="bot-chat-1" />)

    expect(container.querySelector('.wordmark')?.getAttribute('aria-label')).toBe('Alpha')
    expect(container.querySelector('[data-slot="bot_chat_empty"]')?.textContent).toContain(
      translateBots('bot.chatEmpty')
    )

    rerender(<BotChatEmpty sessionId="bot-tip-2" />)

    expect(container.querySelector('[data-slot="bot_chat_empty"]')).not.toBeNull()
  })

  it('offers the three starter prompts in order', () => {
    $lastRoster.set([alpha({ id: 'bot-chat-1', resolved_id: 'bot-chat-1' })])

    const { container } = render(<BotChatEmpty sessionId="bot-chat-1" />)
    const starters = container.querySelector('[data-slot="bot_chat_starters"]') as HTMLElement

    expect(within(starters).getAllByRole('button').map(button => button.textContent)).toEqual(STARTERS)
  })

  it('fills the composer with the chosen starter and sends nothing itself', () => {
    $lastRoster.set([alpha({ id: 'bot-chat-1', resolved_id: 'bot-chat-1' })])

    const { getByRole } = render(<BotChatEmpty sessionId="bot-chat-1" />)

    fireEvent.click(getByRole('button', { name: STARTERS[1] }))

    expect(fillComposer).toHaveBeenCalledTimes(1)
    expect(fillComposer).toHaveBeenCalledWith(STARTERS[1])
  })
})
