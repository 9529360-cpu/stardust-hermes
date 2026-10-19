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

export function stripFtsMarkers(snippet: string): string {
  return snippet.replaceAll('>>>', '').replaceAll('<<<', '')
}

export interface SearchTextPart {
  text: string
  highlighted: boolean
}

/** Parse only the index's plain-text delimiters. Never interpret snippets as
 * HTML. CJK/literal fallback searches have no delimiters, so highlight their
 * literal query instead. The same fallback works for local title matches. */
export function sessionSearchTextParts(text: string, query: string): SearchTextPart[] {
  const marked = [...text.matchAll(/>>>(.*?)<<</gs)]

  if (marked.length > 0) {
    const parts: SearchTextPart[] = []
    let cursor = 0

    for (const match of marked) {
      if (match.index > cursor) {
        parts.push({ text: stripFtsMarkers(text.slice(cursor, match.index)), highlighted: false })
      }

      parts.push({ text: match[1], highlighted: true })
      cursor = match.index + match[0].length
    }

    if (cursor < text.length) {
      parts.push({ text: stripFtsMarkers(text.slice(cursor)), highlighted: false })
    }

    return parts
  }

  const plain = stripFtsMarkers(text)
  const needle = query.trim().toLowerCase()

  if (!needle) {
    return [{ text: plain, highlighted: false }]
  }

  const parts: SearchTextPart[] = []
  const lower = plain.toLowerCase()
  let cursor = 0
  let index = lower.indexOf(needle)

  while (index !== -1) {
    if (index > cursor) {
      parts.push({ text: plain.slice(cursor, index), highlighted: false })
    }

    parts.push({ text: plain.slice(index, index + needle.length), highlighted: true })
    cursor = index + needle.length
    index = lower.indexOf(needle, cursor)
  }

  if (cursor < plain.length) {
    parts.push({ text: plain.slice(cursor), highlighted: false })
  }

  return parts
}

export function searchResultToSession(result: SessionSearchResult): SessionInfo {
  const ts = result.started_at ?? result.session_started ?? 0

  return {
    archived: result.archived ?? false,
    profile: result.profile,
    connection_id: result.connection_id,
    parent_session_id: result.parent_session_id,
    cwd: null,
    ended_at: result.ended_at ?? null,
    id: result.session_id,
    _lineage_root_id: result.lineage_root ?? null,
    input_tokens: result.input_tokens ?? 0,
    is_active: result.is_active ?? false,
    last_active: result.last_active ?? ts,
    message_count: result.message_count ?? 0,
    model: result.model ?? null,
    output_tokens: result.output_tokens ?? 0,
    preview: result.preview ?? (stripFtsMarkers(result.snippet ?? '').trim() || null),
    source: result.source ?? null,
    started_at: ts,
    title: result.title ?? null,
    tool_call_count: result.tool_call_count ?? 0
  }
}

/** Search-only annotation, never written back into the session cache. */
export interface SessionSearchRow extends SessionInfo {
  searchSnippet?: string
}

/** Keep loaded titles and navigation identities, attaching the actual body
 * hit even when the loaded preview is unrelated. Deduplicate both loaded and
 * unloaded compression chains using their durable lineage identity. */
export function mergeSessionSearchResults(
  loaded: readonly SessionInfo[],
  query: string,
  serverMatches: readonly SessionSearchResult[]
): SessionSearchRow[] {
  const out = new Map<string, SessionSearchRow>()
  const byAnyId = new Map<string, SessionInfo>()

  for (const session of loaded) {
    byAnyId.set(session.id, session)
  }

  for (const session of loaded) {
    if (session._lineage_root_id && !byAnyId.has(session._lineage_root_id)) {
      byAnyId.set(session._lineage_root_id, session)
    }

    if (sessionMatchesSearch(session, query)) {
      out.set(session._lineage_root_id || session.id, session)
    }
  }

  for (const match of serverMatches) {
    const row =
      byAnyId.get(match.session_id) ??
      (match.lineage_root ? byAnyId.get(match.lineage_root) : undefined) ??
      searchResultToSession(match)

    const key = row._lineage_root_id || match.lineage_root || row.id
    const existing = out.get(key)

    if (!existing?.searchSnippet) {
      out.set(key, { ...(existing ?? row), searchSnippet: match.snippet || undefined })
    }
  }

  return [...out.values()]
}
