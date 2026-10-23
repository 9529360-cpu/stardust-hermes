import { describe, expect, it } from 'vitest'

import { appViewForPath, CONNECTORS_ROUTE, CRON_ROUTE, legacyMcpRedirect, NEW_CHAT_ROUTE, primaryRouteSelectedSessionId, routeSessionId, sessionRoute, SETTINGS_ROUTE, TASKS_ROUTE } from './routes'

const SESS_A = 'sess-a'
const SESS_B = 'sess-b'

describe('primaryRouteSelectedSessionId', () => {
  it('reserves connectors as a page and redirects legacy MCP links without losing scope or selection', () => {
    expect(appViewForPath(CONNECTORS_ROUTE)).toBe('connectors')
    expect(routeSessionId(CONNECTORS_ROUTE)).toBeNull()
    const target = legacyMcpRedirect('?tab=mcp&server=files%20work&profile=work&connection=remote')!
    const url = new URL(target, 'https://desktop.test')
    expect(url.pathname).toBe('/connectors')
    expect(url.searchParams.get('server')).toBe('files work')
    expect(url.searchParams.get('profile')).toBe('work')
    expect(url.searchParams.get('connection')).toBe('remote')
    expect(url.searchParams.has('tab')).toBe(false)
    expect(legacyMcpRedirect('?tab=skills&server=files')).toBeNull()
  })

  it('routes the task center as a page while preserving the legacy cron page', () => {
    expect(appViewForPath(TASKS_ROUTE)).toBe('tasks')
    expect(routeSessionId(TASKS_ROUTE)).toBeNull()
    expect(appViewForPath(CRON_ROUTE)).toBe('cron')
  })

  it('prefers the routed session id over a stale/different store selection (#59305)', () => {
    // The route already committed to B while the store selection hasn't
    // caught up yet (still reads A) — the route wins.
    expect(primaryRouteSelectedSessionId(sessionRoute(SESS_B), SESS_A)).toBe(SESS_B)
  })

  it('returns null on the new-chat route even with a leftover selection from the previous chat', () => {
    expect(primaryRouteSelectedSessionId(NEW_CHAT_ROUTE, SESS_A)).toBeNull()
  })

  it('falls back to the store selection on a non-chat route (settings, overlays)', () => {
    expect(primaryRouteSelectedSessionId(SETTINGS_ROUTE, SESS_A)).toBe(SESS_A)
  })

  it('falls back to the store selection when the route matches the same session', () => {
    expect(primaryRouteSelectedSessionId(sessionRoute(SESS_A), SESS_A)).toBe(SESS_A)
  })

  it('returns null on a non-chat route with no store selection', () => {
    expect(primaryRouteSelectedSessionId(SETTINGS_ROUTE, null)).toBeNull()
  })
})
