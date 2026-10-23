import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { type ProfileScope, profileScopeKey } from '@/hermes'
import { useI18n } from '@/i18n'
import { probeCache, probeKey } from '@/lib/mcp-probe-cache'
import { getServers } from '@/lib/mcp-servers'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'

import { useHermesConfigRecord } from '../hooks/use-config-record'
import { CapabilitiesView } from '../skills'

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

export function ConnectorsView() {
  return <CapabilitiesView connectorsPage />
}
