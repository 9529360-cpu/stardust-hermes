import { normalize } from '@/lib/text'
import type { SessionInfo, SessionSearchResult } from '@/types/hermes'

import { sessionTitle } from './chat-runtime'
import { sessionSourceSearchTerms } from './session-source'

export function sessionMatchesSearch(session: SessionInfo, query: string): boolean {
  const needle = normalize(query)

  if (!needle) {
    return true
  }

  return [
    session.id,
    session._lineage_root_id ?? '',
    sessionTitle(session),
    session.preview ?? '',
    session.cwd ?? '',
    session.git_branch ?? '',
    ...sessionSourceSearchTerms(session.source)
  ].some(value => value.toLowerCase().includes(needle))
}

// The backend's FTS layer wraps matched terms in literal '>>>' / '<<<'
// highlight markers (sqlite snippet() delimiters — see hermes_state_search.py).
// Session lists render the snippet as plain text, so the markers must be
// stripped or a search for "foo" paints rows titled ">>>foo<<<".
export function stripFtsMarkers(snippet: string): string {
  return snippet.replaceAll('>>>', '').replaceAll('<<<', '')
}

// FTS results cover sessions that aren't in the loaded page; synthesize a
// minimal SessionInfo so they render in the same row component (opening works
// by id; the snippet stands in for the preview).
export function searchResultToSession(result: SessionSearchResult): SessionInfo {
  const ts = result.session_started ?? Date.now() / 1000

  return {
    archived: false,
    cwd: null,
    ended_at: null,
    id: result.session_id,
    _lineage_root_id: result.lineage_root ?? null,
    input_tokens: 0,
    is_active: false,
    last_active: ts,
    message_count: 0,
    model: result.model ?? null,
    output_tokens: 0,
    preview: stripFtsMarkers(result.snippet ?? '').trim() || null,
    source: result.source ?? null,
    started_at: ts,
    title: null,
    tool_call_count: 0
  }
}

/** Loaded sessions matching `query` (instant, client-side) followed by the
 *  backend's full-text hits that are not already listed. A hit for a loaded
 *  conversation reuses its loaded row — found by live id or lineage root — so
 *  one conversation never paints twice; a hit outside the loaded page gets a
 *  synthesized row. */
export function mergeSessionSearchResults(
  loaded: readonly SessionInfo[],
  query: string,
  serverMatches: readonly SessionSearchResult[]
): SessionInfo[] {
  const out = new Map<string, SessionInfo>()
  const byAnyId = new Map<string, SessionInfo>()

  for (const session of loaded) {
    byAnyId.set(session.id, session)
  }

  for (const session of loaded) {
    // A live id wins over another row's lineage root.
    if (session._lineage_root_id && !byAnyId.has(session._lineage_root_id)) {
      byAnyId.set(session._lineage_root_id, session)
    }

    if (sessionMatchesSearch(session, query)) {
      out.set(session.id, session)
    }
  }

  for (const match of serverMatches) {
    const row =
      byAnyId.get(match.session_id) ??
      (match.lineage_root ? byAnyId.get(match.lineage_root) : undefined) ??
      searchResultToSession(match)

    if (!out.has(row.id)) {
      out.set(row.id, row)
    }
  }

  return [...out.values()]
}
