/**
 * Bot Mode is on screen while the roster page is open, while a bot open is in
 * flight, or while a bot chat owns the center that no full page holds.
 *
 * The chat claim outlives the chat: a page takes the center without moving
 * focus, so the claim must stop counting there. An open's own navigation
 * unmounts the roster page, so an open in flight holds Bot Mode on through it.
 */
import { describe, expect, it } from 'vitest'

import { botModeOnScreen } from './bot-state'

const idle = { centerPage: false, chat: false, page: false, pending: false }

describe('botModeOnScreen', () => {
  it('is on while the roster page is open, whatever holds the center', () => {
    expect(botModeOnScreen({ ...idle, centerPage: true, page: true })).toBe(true)
  })

  it('is on while a bot chat owns the center', () => {
    expect(botModeOnScreen({ ...idle, chat: true })).toBe(true)
  })

  it('is off once another full page takes the center from a bot chat', () => {
    expect(botModeOnScreen({ ...idle, centerPage: true, chat: true })).toBe(false)
  })

  it('is off once the roster page is left for another page', () => {
    expect(botModeOnScreen({ ...idle, centerPage: true })).toBe(false)
  })

  it('stays on through the navigation an open makes, which unmounts the roster page', () => {
    // The roster has unmounted, and the workspace may not report the session route yet.
    expect(botModeOnScreen({ ...idle, centerPage: true, pending: true })).toBe(true)
    expect(botModeOnScreen({ ...idle, pending: true })).toBe(true)
  })
})
