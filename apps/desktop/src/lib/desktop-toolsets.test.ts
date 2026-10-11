import { describe, expect, it } from 'vitest'

import { isDesktopToolsetRow, isDesktopToolsetVisible } from './desktop-toolsets'

describe('isDesktopToolsetVisible', () => {
  it('hides platform-coupled and internal toolsets', () => {
    for (const name of ['discord', 'discord_admin', 'yuanbao', 'context_engine', 'moa']) {
      expect(isDesktopToolsetVisible(name)).toBe(false)
    }
  })

  it('keeps ordinary user-facing toolsets', () => {
    for (const name of ['web', 'browser', 'terminal', 'file', 'memory', 'vision', 'image_gen']) {
      expect(isDesktopToolsetVisible(name)).toBe(true)
    }
  })
})

describe('isDesktopToolsetRow', () => {
  it('lists a toolset as a toggle only when it has tools behind it', () => {
    expect(isDesktopToolsetRow({ name: 'web', tools: ['web_search', 'web_extract'] })).toBe(true)
    // Speech-to-text is config-only: no tool schemas, and its switch lives in Settings.
    expect(isDesktopToolsetRow({ name: 'stt', tools: [] })).toBe(false)
  })

  it('keeps the curated-out toolsets hidden even when they have tools', () => {
    expect(isDesktopToolsetRow({ name: 'discord', tools: ['discord'] })).toBe(false)
  })
})
