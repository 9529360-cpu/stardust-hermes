import { describe, expect, it } from 'vitest'

import type { OAuthProvider } from '@/types/hermes'

import { sortProviders } from './providers'

const provider = (
  id: string,
  name = id,
  status: OAuthProvider['status'] = { logged_in: false }
): OAuthProvider => ({
  cli_command: '',
  docs_url: '',
  flow: 'device_code',
  id,
  name,
  status
})

describe('sortProviders', () => {
  it('removes the inherited Nous login and anonymous free-tier identity from Desktop provider surfaces', () => {
    const input = [
      provider('nous', 'Nous Portal'),
      provider('nous', 'Nous Free Tier', { logged_in: true, free_tier: true }),
      provider('qwen-oauth', 'Qwen'),
      provider('openai-codex', 'Codex')
    ]

    expect(sortProviders(input).map(row => row.id)).toEqual(['openai-codex', 'qwen-oauth'])
    expect(input).toHaveLength(4)
  })

  it('keeps an already-authenticated real Nous account visible for management', () => {
    const input = [
      provider('qwen-oauth', 'Qwen'),
      provider('nous', 'Nous Portal', { logged_in: true, free_tier: false })
    ]

    expect(sortProviders(input).map(row => row.id)).toEqual(['qwen-oauth', 'nous'])
  })

  it('keeps user-selected OAuth providers in Stardust order', () => {
    const input = [provider('claude-code', 'Claude'), provider('xai-oauth', 'Grok'), provider('minimax-oauth', 'MiniMax')]

    expect(sortProviders(input).map(row => row.id)).toEqual(['minimax-oauth', 'xai-oauth', 'claude-code'])
  })
})
