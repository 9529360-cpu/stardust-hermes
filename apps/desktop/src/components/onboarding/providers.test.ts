import { describe, expect, it } from 'vitest'

import type { OAuthProvider } from '@/types/hermes'

import { sortProviders } from './providers'

const provider = (id: string, name = id): OAuthProvider => ({
  cli_command: '',
  docs_url: '',
  flow: 'device_code',
  id,
  name,
  status: { logged_in: false }
})

describe('sortProviders', () => {
  it('removes the inherited Nous account from Stardust Desktop provider surfaces', () => {
    const input = [provider('nous', 'Nous Portal'), provider('qwen-oauth', 'Qwen'), provider('openai-codex', 'Codex')]

    expect(sortProviders(input).map(row => row.id)).toEqual(['openai-codex', 'qwen-oauth'])
    expect(input.map(row => row.id)).toEqual(['nous', 'qwen-oauth', 'openai-codex'])
  })

  it('keeps user-selected OAuth providers in Stardust order', () => {
    const input = [provider('claude-code', 'Claude'), provider('xai-oauth', 'Grok'), provider('minimax-oauth', 'MiniMax')]

    expect(sortProviders(input).map(row => row.id)).toEqual(['minimax-oauth', 'xai-oauth', 'claude-code'])
  })
})
