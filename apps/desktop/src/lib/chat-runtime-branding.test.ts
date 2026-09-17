import { describe, expect, it } from 'vitest'

import type { SessionInfo } from '@/types/hermes'

import { sessionTitle } from './chat-runtime'

const session = (title: string): SessionInfo => ({ title } as SessionInfo)

describe('sessionTitle product branding', () => {
  it('aliases the persisted legacy welcome title without changing ordinary task titles', () => {
    expect(sessionTitle(session('Welcome to Hermes'))).toBe('Welcome to Stardust')
    expect(sessionTitle(session('Bot Chat'))).toBe('Agent workspace')
    expect(sessionTitle(session('Refactor the desktop shell'))).toBe('Refactor the desktop shell')
  })
})
