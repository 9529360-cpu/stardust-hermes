import fs from 'node:fs'

import { net as electronNet } from 'electron'

import { type FaviconIo, resolveFavicon } from '../favicon'

import { TITLE_BYTE_BUDGET, TITLE_USER_AGENT } from './link-title'

export function createFaviconCache(FAVICON_CACHE_PATH: string) {
  // ─── Favicon resolution ──────────────────────────────────────────────────────
  // The ladder itself is electron/favicon.ts; this is its I/O, its cache, and
  // the one rule that belongs to the app rather than the algorithm: one icon
  // per host. A connector's mark doesn't vary by path, and hosting the cache on
  // the host key means Linear's docs page and Linear's MCP endpoint cost one
  // lookup between them.

  const FAVICON_CACHE_LIMIT = 400
  const FAVICON_TTL_MS = 30 * 24 * 60 * 60 * 1000
  // A miss is cheap to re-check and expensive to be wrong about (a site that
  // was behind a captcha yesterday has a logo today), so it expires fast.
  const FAVICON_MISS_TTL_MS = 12 * 60 * 60 * 1000
  const FAVICON_TIMEOUT_MS = 6000
  const FAVICON_MAX_BYTES = 256 * 1024
  const FAVICON_WRITE_DEBOUNCE_MS = 3000

  let faviconCache: Map<string, { at: number; icon: string }> | null = null
  let faviconWriteTimer: null | ReturnType<typeof setTimeout> = null
  const faviconInflight = new Map<string, Promise<string>>()

  function faviconCacheKey(rawUrl: string): string {
    try {
      return new URL(rawUrl).hostname.replace(/^www\./i, '').toLowerCase()
    } catch {
      return ''
    }
  }

  function loadFaviconCache(): Map<string, { at: number; icon: string }> {
    if (faviconCache) {
      return faviconCache
    }

    faviconCache = new Map()

    try {
      const raw = JSON.parse(fs.readFileSync(FAVICON_CACHE_PATH, 'utf8'))

      for (const [host, entry] of Object.entries(raw?.icons ?? {})) {
        const at = Number((entry as { at?: number })?.at)
        const icon = String((entry as { icon?: string })?.icon ?? '')

        if (Number.isFinite(at) && Date.now() - at < (icon ? FAVICON_TTL_MS : FAVICON_MISS_TTL_MS)) {
          faviconCache.set(host, { at, icon })
        }
      }
    } catch {
      // No cache yet, or it's unreadable — resolving again is the whole cost.
    }

    return faviconCache
  }

  function saveFaviconCacheSoon() {
    if (faviconWriteTimer) {
      return
    }

    faviconWriteTimer = setTimeout(() => {
      faviconWriteTimer = null

      try {
        const icons = Object.fromEntries(loadFaviconCache())

        fs.writeFileSync(FAVICON_CACHE_PATH, JSON.stringify({ icons }), 'utf8')
      } catch {
        // Cache is an optimization; failing to persist it costs one refetch.
      }
    }, FAVICON_WRITE_DEBOUNCE_MS)

    faviconWriteTimer.unref?.()
  }

  async function faviconFetch(url: string, accept: string) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), FAVICON_TIMEOUT_MS)

    try {
      return await electronNet.fetch(url, {
        // Same browser-shaped identity the title fetcher uses: a plain Electron
        // UA gets a challenge page from anything behind a bot wall.
        headers: { Accept: accept, 'Accept-Language': 'en-US,en;q=0.7', 'User-Agent': TITLE_USER_AGENT },
        redirect: 'follow',
        signal: controller.signal
      })
    } finally {
      clearTimeout(timer)
    }
  }

  const faviconIo: FaviconIo = {
    fetchImage: async url => {
      const response = await faviconFetch(url, 'image/avif,image/webp,image/svg+xml,image/*;q=0.8,*/*;q=0.5')

      if (!response.ok) {
        return null
      }

      const buffer = await response.arrayBuffer()

      if (buffer.byteLength === 0 || buffer.byteLength > FAVICON_MAX_BYTES) {
        return null
      }

      return { bytes: new Uint8Array(buffer), mime: response.headers.get('content-type') ?? '' }
    },
    fetchText: async url => {
      const response = await faviconFetch(url, 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5')

      return response.ok ? (await response.text()).slice(0, TITLE_BYTE_BUDGET * 2) : ''
    }
  }

  function resolveFaviconCached(rawUrl: string): Promise<string> {
    const key = faviconCacheKey(String(rawUrl || '').trim())

    if (!key) {
      return Promise.resolve('')
    }

    const cache = loadFaviconCache()
    const hit = cache.get(key)

    if (hit && Date.now() - hit.at < (hit.icon ? FAVICON_TTL_MS : FAVICON_MISS_TTL_MS)) {
      return Promise.resolve(hit.icon)
    }

    const inflight = faviconInflight.get(key)

    if (inflight) {
      return inflight
    }

    const pending = resolveFavicon(rawUrl, faviconIo)
      .catch(() => '')
      .then(icon => {
        if (cache.size >= FAVICON_CACHE_LIMIT) {
          cache.delete(cache.keys().next().value)
        }

        cache.set(key, { at: Date.now(), icon })
        saveFaviconCacheSoon()
        faviconInflight.delete(key)

        return icon
      })

    faviconInflight.set(key, pending)

    return pending
  }

  return resolveFaviconCached
}
