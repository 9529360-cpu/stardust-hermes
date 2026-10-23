import { useEffect, useState } from 'react'

import { getStatus } from '@/hermes'

/** Read the connected backend, never the runtime source installed beside Electron. */
export function useBackendVersion(scope: string, gatewayState: string) {
  const [result, setResult] = useState<{ scope: string; version: string | null } | null>(null)

  useEffect(() => {
    let cancelled = false

    setResult(null)

    const refresh = async () => {
      let version: string | null = null

      try {
        const status = await getStatus()

        version = status.version?.trim() || null
      } catch {
        // A failed status read must not display a previous backend's version.
      }

      if (!cancelled) {
        setResult({ scope, version })
      }
    }

    void refresh()

    return () => {
      cancelled = true
    }
  }, [scope, gatewayState])

  return result?.scope === scope ? result.version : null
}
