import { describe, expect, it } from 'vitest'

import type { SessionInfo, SessionSearchResult } from '@/types/hermes'

import { mergeSessionSearchResults, sessionMatchesSearch, stripFtsMarkers } from './session-search'

function makeSession(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    archived: false,
    cwd: '/home/user/projects/hermes-agent',
    ended_at: null,
    id: '20260603_090200_abcd12',
    input_tokens: 0,
    is_active: false,
    last_active: 1_000,
    message_count: 2,
    model: 'claude',
    output_tokens: 0,
    preview: 'Fix Desktop session search',
    source: 'cli',
    started_at: 1_000,
    title: 'Desktop Search Feature',
    tool_call_count: 0,
    ...overrides
  }
}

describe('sessionMatchesSearch', () => {
  it('matches loaded sessions by full and partial session id', () => {
    const session = makeSession()

    expect(sessionMatchesSearch(session, '20260603_090200_abcd12')).toBe(true)
    expect(sessionMatchesSearch(session, '090200')).toBe(true)
    expect(sessionMatchesSearch(session, 'ABCD12')).toBe(true)
  })

  it('matches projected compression sessions by lineage root id', () => {
    const session = makeSession({
      _lineage_root_id: '20260602_235959_root99',
      id: '20260603_010000_tip01'
    })

    expect(sessionMatchesSearch(session, 'root99')).toBe(true)
    expect(sessionMatchesSearch(session, '20260602')).toBe(true)
  })

  it('preserves title, preview, and workspace matching', () => {
    const session = makeSession()

    expect(sessionMatchesSearch(session, 'desktop search')).toBe(true)
    expect(sessionMatchesSearch(session, 'session search')).toBe(true)
    expect(sessionMatchesSearch(session, 'hermes-agent')).toBe(true)
  })

  it('matches sessions by git branch', () => {
    expect(sessionMatchesSearch(makeSession({ git_branch: 'feat/cool-thing' }), 'feat/cool-thing')).toBe(true)
    expect(sessionMatchesSearch(makeSession({ git_branch: 'feat/cool-thing' }), 'cool')).toBe(true)
    expect(sessionMatchesSearch(makeSession({ git_branch: 'main' }), 'main')).toBe(true)
  })

  it('matches sessions by source platform and aliases', () => {
    expect(sessionMatchesSearch(makeSession({ source: 'telegram' }), 'Telegram')).toBe(true)
    expect(sessionMatchesSearch(makeSession({ source: 'whatsapp' }), 'WhatsApp')).toBe(true)
    expect(sessionMatchesSearch(makeSession({ source: 'whatsapp' }), 'wa')).toBe(true)
    expect(sessionMatchesSearch(makeSession({ source: 'slack' }), 'slack')).toBe(true)
    expect(sessionMatchesSearch(makeSession({ source: 'bluebubbles' }), 'imessage')).toBe(true)
  })

  it('does not match unrelated queries', () => {
    expect(sessionMatchesSearch(makeSession(), 'totally-unrelated')).toBe(false)
  })
})

// Regression: the backend's session-search FTS layer wraps matched terms in
// literal '>>>' / '<<<' snippet() delimiters (hermes_state_search.py). Session
// lists paint the snippet as plain text, so an unstripped marker renders rows
// titled ">>>foo<<<" (Aug 2026 desktop audit).
describe('stripFtsMarkers', () => {
  it('strips highlight markers around the matched term', () => {
    expect(stripFtsMarkers('...replied with >>>MARCO<<< and nothing else...')).toBe(
      '...replied with MARCO and nothing else...'
    )
  })

  it('strips multiple marked terms', () => {
    expect(stripFtsMarkers('>>>alpha<<< then >>>beta<<<')).toBe('alpha then beta')
  })

  it('leaves marker-free snippets untouched', () => {
    expect(stripFtsMarkers('plain snippet text')).toBe('plain snippet text')
  })

  it('handles empty string', () => {
    expect(stripFtsMarkers('')).toBe('')
  })
})

function makeMatch(overrides: Partial<SessionSearchResult> = {}): SessionSearchResult {
  return {
    lineage_root: null,
    model: null,
    role: 'assistant',
    session_id: 'older-unloaded',
    session_started: 900,
    snippet: 'we chose the >>>harbor<<< route',
    source: 'telegram',
    ...overrides
  }
}

describe('mergeSessionSearchResults', () => {
  it('lists client matches first, then full-text hits outside the loaded page', () => {
    const loaded = makeSession({ id: 'loaded-hit', title: 'Harbor planning' })

    const rows = mergeSessionSearchResults(
      [loaded, makeSession({ id: 'loaded-miss', preview: null, title: 'Other' })],
      'harbor',
      [makeMatch()]
    )

    expect(rows.map(row => row.id)).toEqual(['loaded-hit', 'older-unloaded'])
    expect(rows[1]).toMatchObject({ preview: 'we chose the harbor route', source: 'telegram', title: null })
  })

  it('reuses the loaded row for a hit on a loaded conversation, by live id or lineage root', () => {
    const tip = makeSession({ _lineage_root_id: 'root-1', id: 'tip-1', preview: null, title: 'Compressed chat' })

    const rows = mergeSessionSearchResults([tip], 'needle-only-in-messages', [
      makeMatch({ session_id: 'tip-1' }),
      makeMatch({ lineage_root: 'root-1', session_id: 'pre-compression-id' })
    ])

    expect(rows).toEqual([tip])
  })

  it('never lists one conversation twice when the client and the server both match it', () => {
    const loaded = makeSession({ id: 'both', title: 'Harbor notes' })

    expect(mergeSessionSearchResults([loaded], 'harbor', [makeMatch({ session_id: 'both' })])).toEqual([loaded])
  })
})
