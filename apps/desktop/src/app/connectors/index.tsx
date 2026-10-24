import { useStore } from '@nanostores/react'
import { lazy, Suspense, useEffect, useState } from 'react'

import { type ProfileScope, profileScopeKey } from '@/hermes'
import { useI18n } from '@/i18n'
import { probeCache, probeKey } from '@/lib/mcp-probe-cache'
import { getServers } from '@/lib/mcp-servers'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'

import { useHermesConfigRecord } from '../hooks/use-config-record'
import { CapabilitiesView } from '../skills'

// The CX5 catalog page is code-split: the flag-off path never downloads it.
const ConnectorCatalogView = lazy(async () => ({ default: (await import('./connector-catalog')).ConnectorCatalogView }))

// Read the same scoped config/query and probe cache as McpTab. No extra
// discovery or status requests: a configured or failed server never counts.
export function ConnectedSummary({ profile }: { profile: ProfileScope }) {
  const { t } = useI18n()
  const activeProfile = useStore($activeGatewayProfile)
  const { data } = useHermesConfigRecord(profile)
  const [, tick] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => tick(value => value + 1), 1000)

    return () => window.clearInterval(timer)
  }, [])
  const scope = profile != null ? profileScopeKey(profile) : normalizeProfileKey(activeProfile)

  const count = Object.entries(getServers(data ?? null)).filter(
    ([name, server]) => server.enabled !== false && probeCache.get(probeKey(name, server, scope))?.result.ok
  ).length

  return <span className="shrink-0 text-sm text-(--ui-text-tertiary)">{t.connectors.connectedCount(count)}</span>
}

// CX5 opt-in: `connectors.unified_dialog` in config.yaml. Off (or unreadable)
// = the pre-CX5 Capabilities surface, byte-identical. Pure so tests and the
// e2e suite can pin it directly.
export function connectorsUnifiedDialogEnabled(config: Record<string, unknown> | null | undefined): boolean {
  const section = config?.connectors

  return Boolean(
    typeof section === 'object' && section !== null && (section as Record<string, unknown>).unified_dialog === true
  )
}

export function ConnectorsView() {
  // Same config read the rest of the page uses; while it is pending the flag
  // cannot be on yet, so the legacy view renders exactly as before.
  const { data } = useHermesConfigRecord()

  if (!connectorsUnifiedDialogEnabled(data)) {
    return <CapabilitiesView connectorsPage />
  }

  return (
    <Suspense fallback={null}>
      <ConnectorCatalogView profile={null} />
    </Suspense>
  )
}
