/**
 * CX5 connector catalog page (flag-gated, connectors.unified_dialog): a
 * service-catalog grid + connected list + one unified add/connect dialog.
 * Mounts ONLY when the opt-in flag is on; the flag-off ConnectorsView keeps
 * rendering the legacy Capabilities surface byte-identically.
 */

import { useStore } from '@nanostores/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'

import { getMcpCatalog } from '@/api/mcp'
import { AvatarChip } from '@/components/ui/avatar-chip'
import { Button } from '@/components/ui/button'
import { ErrorBanner, ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { type ProfileScope, profileScopeKey, testMcpServer } from '@/hermes'
import { useI18n } from '@/i18n'
import { brandFor } from '@/lib/mcp-brands'
import { freshProbe, probeCache, probeKey } from '@/lib/mcp-probe-cache'
import { getServers } from '@/lib/mcp-servers'
import { $activeGatewayProfile } from '@/store/profile'
import type { McpCatalogEntry } from '@/types/hermes'

import { hermesConfigKey } from '../hooks/use-config-record'
import { useHermesConfigRecord } from '../hooks/use-config-record'

import { ConnectorAddDialog, CustomConnectorAddDialog } from './connector-add-dialog'

import { ConnectedSummary } from './index'

/** One catalog card: name, description, auth tag, runtime tag, publisher. */
function CatalogCard({ entry, onConnect }: { entry: McpCatalogEntry; onConnect: () => void }) {
  const { t } = useI18n()
  const c = t.connectors
  const runtime = entry.runtime ?? (entry.transport === 'stdio' ? ('local' as const) : ('remote' as const))
  const brand = brandFor(entry.name)

  return (
    <div
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-chat-surface-background) p-3"
      data-entry={entry.name}
      data-slot="connector-catalog-card"
    >
      <div className="flex items-center gap-2">
        <AvatarChip brand={brand} name={entry.name} />
        <span className="min-w-0 truncate text-sm font-medium text-foreground/90">{entry.name}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {entry.auth_type === 'oauth' && (
            <span className="rounded bg-(--ui-bg-tertiary) px-1.5 py-0.5 text-[0.6rem] text-(--ui-text-secondary)">
              OAuth
            </span>
          )}
          {entry.auth_type === 'api_key' && (
            <span className="rounded bg-(--ui-bg-tertiary) px-1.5 py-0.5 text-[0.6rem] text-(--ui-text-secondary)">
              {c.apiKeyLabel}
            </span>
          )}
          <span className="rounded bg-(--ui-bg-tertiary) px-1.5 py-0.5 text-[0.6rem] text-(--ui-text-secondary)">
            {runtime === 'local' ? c.runtimeLocal : c.runtimeRemote}
          </span>
        </span>
      </div>
      <p className="line-clamp-2 min-h-8 text-xs text-(--ui-text-secondary)">{entry.description}</p>
      {entry.publisher ? (
        <p className="truncate text-[0.65rem] text-(--ui-text-tertiary)">
          {c.publisher}: {entry.publisher}
        </p>
      ) : null}
      <div className="mt-auto flex items-center justify-between">
        <span className="text-[0.65rem] text-(--ui-text-tertiary)">
          {entry.installed ? c.connectedOk : c.notConnectedYet}
        </span>
        <Button data-slot={`connect-${entry.name}`} disabled={entry.installed} onClick={onConnect} size="xs">
          {c.connect}
        </Button>
      </div>
    </div>
  )
}

/** One connected/configured server row: status, tool count, last check time. */
function ConnectedRow({ name, server, scopeKey }: { name: string; server: Record<string, unknown>; scopeKey: string }) {
  const { t } = useI18n()
  const c = t.connectors
  const enabled = server.enabled !== false
  const probe = probeCache.get(probeKey(name, server, scopeKey))
  const [, tick] = useState(0)

  // Re-read the probe cache periodically so 上次检查时间 stays fresh while mounted.
  useEffect(() => {
    const timer = window.setInterval(() => tick(value => value + 1), 5000)

    return () => window.clearInterval(timer)
  }, [])

  const checkedAt = probe ? new Date(probe.at).toLocaleTimeString() : null

  return (
    <div
      className="flex items-center gap-2 rounded-md px-2 py-2"
      data-server={name}
      data-slot="connector-connected-row"
    >
      <AvatarChip brand={brandFor(name)} name={name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[0.78rem] font-medium text-foreground/85">{name}</span>
          <span
            className={
              probe?.result.ok && enabled
                ? 'text-[0.62rem] text-(--ui-green-text)'
                : 'text-[0.62rem] text-(--ui-text-tertiary)'
            }
          >
            {probe?.result.ok && enabled ? c.connectedOk : enabled ? c.notConnectedYet : c.disabled}
          </span>
        </div>
        <span className="block truncate text-[0.62rem] text-muted-foreground/60">
          {probe?.result.ok
            ? `${c.availableTools}: ${probe.result.tools.length}${checkedAt ? `, ${c.lastChecked(checkedAt)}` : ''}`
            : server.url
              ? String(server.url)
              : String(server.command ?? '')}
        </span>
      </div>
    </div>
  )
}

export function ConnectorCatalogView({ profile }: { profile: ProfileScope | null }) {
  const { t } = useI18n()
  const c = t.connectors
  const activeProfile = useStore($activeGatewayProfile)
  const scopeProfile = profile ?? activeProfile
  const scopeKey = profileScopeKey(scopeProfile)
  const { data: configRecord } = useHermesConfigRecord(scopeProfile)
  const queryClient = useQueryClient()
  const [, tick] = useState(0)

  const catalogQuery = useQuery({
    queryKey: ['mcp-catalog', scopeKey],
    queryFn: () => getMcpCatalog(scopeProfile ?? undefined)
  })

  const [query, setQuery] = useState('')
  const [dialogEntry, setDialogEntry] = useState<McpCatalogEntry | null>(null)
  const [customOpen, setCustomOpen] = useState(false)

  const entries = useMemo(() => catalogQuery.data?.entries ?? [], [catalogQuery.data])

  const visibleEntries = useMemo(() => {
    const q = query.trim().toLowerCase()

    if (!q) {
      return entries
    }

    return entries.filter(
      entry =>
        entry.name.toLowerCase().includes(q) ||
        entry.description.toLowerCase().includes(q) ||
        (entry.publisher ?? '').toLowerCase().includes(q)
    )
  }, [entries, query])

  const servers = useMemo(() => Object.entries(getServers(configRecord ?? null)), [configRecord])

  // Refresh the probe cache read when probes land after mount.
  useEffect(() => {
    const timer = window.setInterval(() => tick(value => value + 1), 5000)

    return () => window.clearInterval(timer)
  }, [])

  // Auto-probe on mount / server-set change, mirroring the MCP tab: a fresh
  // install (or an expired cache) otherwise reads as 未连接 with no tool count
  // until a manual recheck. Failures land in the cache too — the row shows the
  // error state, and 已连接 counts only successful handshakes.
  const probedRef = useRef(new Set<string>())
  useEffect(() => {
    for (const [name, server] of servers) {
      if (server.enabled === false || probedRef.current.has(name)) {
        continue
      }

      const key = probeKey(name, server, scopeKey)

      if (freshProbe(key) === null) {
        probedRef.current.add(name)
        void testMcpServer(name, scopeProfile ?? undefined)
          .catch(() => null)
          .then(result => {
            if (result) {
              probeCache.set(key, { at: Date.now(), result })
              tick(value => value + 1)
            }
          })
      }
    }
    // servers identity changes only when the config record does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [servers])

  const refreshCatalog = () => {
    void catalogQuery.refetch()
  }

  const onConnected = () => {
    refreshCatalog()
    // The connected list reads the config record; a fresh install wrote
    // mcp_servers there, so invalidate or the new row never shows until remount.
    void queryClient.invalidateQueries({ queryKey: hermesConfigKey(scopeProfile) })
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto" data-slot="connector-catalog-page">
      <header className="flex shrink-0 items-start justify-between gap-4 px-6 py-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-(--ui-text-primary)">{t.connectors.pageTitle}</h1>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ConnectedSummary profile={scopeProfile} />
          <Button data-slot="add-connection" onClick={() => setCustomOpen(true)} size="sm">
            {c.addConnection}
          </Button>
        </div>
      </header>

      <section className="shrink-0 px-6 pb-2" data-slot="connector-connected-section">
        <h2 className="mb-1 text-[0.72rem] font-medium text-(--ui-text-tertiary)">{c.connectedList}</h2>
        {servers.length === 0 ? (
          <p className="px-1 py-2 text-xs text-(--ui-text-tertiary)">{c.nothingYet}</p>
        ) : (
          servers.map(([name, server]) => <ConnectedRow key={name} name={name} scopeKey={scopeKey} server={server} />)
        )}
      </section>

      <section className="min-h-0 flex-1 px-6 pb-6" data-slot="connector-catalog-section">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-[0.72rem] font-medium text-(--ui-text-tertiary)">{c.catalogTitle}</h2>
          <Input
            className="w-56"
            data-slot="connector-catalog-search"
            onChange={event => setQuery(event.target.value)}
            placeholder={c.search}
            value={query}
          />
        </div>
        {catalogQuery.isLoading ? (
          <p className="px-1 py-4 text-xs text-(--ui-text-tertiary)">{c.probing}</p>
        ) : catalogQuery.isError && !catalogQuery.data ? (
          <ErrorState
            className="mx-auto max-w-sm py-8"
            description={c.catalogLoadFailedHelp}
            title={c.catalogLoadFailed}
          >
            <Button onClick={() => void refreshCatalog()} size="sm" variant="outline">
              {t.common.retry}
            </Button>
          </ErrorState>
        ) : (
          <>
            {catalogQuery.isError && (
              <ErrorBanner className="mb-3">
                {c.catalogRefreshFailed}{' '}
                <Button onClick={() => void refreshCatalog()} size="xs" variant="ghost">
                  {t.common.retry}
                </Button>
              </ErrorBanner>
            )}
            {visibleEntries.length === 0 ? (
              <p className="px-1 py-4 text-xs text-(--ui-text-tertiary)">{c.empty}</p>
            ) : (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-2">
                {visibleEntries.map(entry => (
                  <CatalogCard entry={entry} key={entry.name} onConnect={() => setDialogEntry(entry)} />
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <ConnectorAddDialog
        entry={dialogEntry}
        onClose={() => setDialogEntry(null)}
        onConnected={onConnected}
        profile={scopeProfile ?? undefined}
      />
      <CustomConnectorAddDialog
        onClose={() => setCustomOpen(false)}
        onConnected={onConnected}
        open={customOpen}
        profile={scopeProfile ?? undefined}
      />
    </div>
  )
}
