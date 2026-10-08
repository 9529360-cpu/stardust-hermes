import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { type ProfileScope, profileScopeKey } from '@/hermes'
import { useI18n } from '@/i18n'
import { requestGatewayForProfile } from '@/store/gateway'
import { notify, notifyError } from '@/store/notifications'
import { $activeSessionId } from '@/store/session'

import { ListRow } from './primitives'

const PLAYWRIGHT_MCP_PACKAGE = '@playwright/mcp@0.0.83'
const DEFAULT_CHROME_PROFILE_DIR = 'Default'

const BROWSER_CONTROL_CAPABILITIES = [
  'browser_navigate',
  'browser_snapshot',
  'browser_click',
  'browser_type',
  'browser_scroll',
  'browser_press',
  'browser_back',
  'browser_tabs'
]

type BridgeStatus = 'inactive' | 'starting' | 'connected' | 'stopping' | 'error'

type BrowserBridgeStatus = {
  state?: BridgeStatus | 'stopped'
  status?: BridgeStatus
  error?: string
  session_id?: string
  controller_id?: string
  browser_profile_id?: string
  capabilities?: string[]
}

interface BrowserHostControlPanelProps {
  profile?: ProfileScope
}

function profileName(profile: ProfileScope | undefined): string {
  if (!profile) {
    return 'default'
  }
  const key = profileScopeKey(profile)

  return key || 'default'
}

function statusKey(status: BridgeStatus | 'stopped' | undefined): BridgeStatus {
  if (status === 'connected') {
    return 'connected'
  }

  if (status === 'starting') {
    return 'starting'
  }

  if (status === 'stopping') {
    return 'stopping'
  }

  if (status === 'error') {
    return 'error'
  }

  return 'inactive'
}

/**
 * Explicit pairing for the existing Chrome host controller. The backend mints
 * the session-bound one-time grant; Electron owns the bridge process and the
 * Playwright child never receives that grant.
 */
export function BrowserHostControlPanel({ profile }: BrowserHostControlPanelProps) {
  const { t } = useI18n()
  const copy = t.settings.toolsets.browserHostControl
  const activeSessionId = useStore($activeSessionId)
  const profileKey = useMemo(() => profileName(profile), [profile])
  const [chromeProfileDir, setChromeProfileDir] = useState(DEFAULT_CHROME_PROFILE_DIR)
  const [status, setStatus] = useState<BrowserBridgeStatus>({ state: 'inactive', status: 'inactive' })
  const [controllerId, setControllerId] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    if (!activeSessionId) {
      setStatus({ state: 'inactive', status: 'inactive' })

      return
    }

    try {
      const result = await requestGatewayForProfile<BrowserBridgeStatus>(
        profileKey,
        'browser.controller.bridge_status',
        { session_id: activeSessionId }
      )

      setStatus(result || { state: 'inactive', status: 'inactive' })

      if (result?.controller_id) {
        setControllerId(result.controller_id)
      }
    } catch (error) {
      setStatus({ state: 'error', error: error instanceof Error ? error.message : String(error) })
    }
  }, [activeSessionId, profileKey])

  useEffect(() => {
    void refresh()
    const off = window.hermesDesktop?.browserControl?.onStatus?.(next => setStatus(next))

    return () => off?.()
  }, [refresh])

  useEffect(() => {
    if (status.status !== 'starting' && status.state !== 'starting') {return}
    let active = true

    const timer = window.setInterval(async () => {
      try {
        const next = await window.hermesDesktop.browserControl.status()

        if (active) {
          setStatus(next)
        }
      } catch {
        // The backend status refresh below remains authoritative for the session.
      }
    }, 500)

    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [status.state, status.status])

  const connect = useCallback(async () => {
    if (!activeSessionId) {
      notifyError(new Error(copy.noSession), copy.connectFailed)

      return
    }

    const profileDir = chromeProfileDir.trim()

    if (!/^[\w .-]{1,90}$/.test(profileDir)) {
      notifyError(new Error(copy.invalidProfile), copy.connectFailed)

      return
    }

    setBusy(true)

    try {
      const browserProfileId = `chrome-${profileDir}`

      const prepared = await requestGatewayForProfile<Record<string, unknown>>(
        profileKey,
        'browser.controller.bridge_prepare',
        {
          session_id: activeSessionId,
          browser_profile_id: browserProfileId,
          capabilities: BROWSER_CONTROL_CAPABILITIES,
          protocol_version: 1
        }
      )

      const launchContext = prepared?.launch_context || prepared

      const started = await window.hermesDesktop.browserControl.start({
        profile: profileKey,
        launchContext,
        chromeProfileDir: profileDir,
        packageSpec: PLAYWRIGHT_MCP_PACKAGE
      })

      setStatus(started)

      if (started?.controller_id) {
        setControllerId(started.controller_id)
      }
      notify({ kind: 'info', title: copy.connectedTitle, message: copy.connectedMessage })
    } catch (error) {
      setStatus({ state: 'error', error: error instanceof Error ? error.message : String(error) })
      notifyError(error, copy.connectFailed)
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, chromeProfileDir, copy, profileKey])

  const disconnect = useCallback(async () => {
    if (!activeSessionId) {return}

    setBusy(true)

    try {
      const stopped = await window.hermesDesktop.browserControl.stop()
      setStatus(stopped)
    } catch (error) {
      setStatus({ state: 'error', error: error instanceof Error ? error.message : String(error) })
      notifyError(error, copy.disconnectFailed)
    } finally {
      try {
        await requestGatewayForProfile(profileKey, 'browser.controller.bridge_revoke', {
          session_id: activeSessionId,
          ...(controllerId ? { controller_id: controllerId } : {})
        })
      } catch (error) {
        notifyError(error, copy.disconnectFailed)
      }

      setControllerId('')
      setBusy(false)
      await refresh()
    }
  }, [activeSessionId, controllerId, copy.disconnectFailed, profileKey, refresh])

  const state = statusKey(status.status || status.state)
  const isConnected = state === 'connected'
  const isBusy = busy || state === 'starting' || state === 'stopping'

  return (
    <ListRow
      below={
        <div className="grid max-w-xl gap-2 pt-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-(--ui-text-tertiary)">
            <span>{copy.statusLabel}: {copy.status[state]}</span>
            {status.error && <span className="max-w-full truncate text-(--ui-text-tertiary)">{status.error}</span>}
          </div>
          <label className="grid gap-1 text-xs text-(--ui-text-secondary)">
            <span>{copy.profileLabel}</span>
            <Input
              aria-label={copy.profileLabel}
              disabled={isBusy || isConnected}
              onChange={event => setChromeProfileDir(event.target.value)}
              value={chromeProfileDir}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button disabled={isBusy || isConnected || !activeSessionId} onClick={() => void connect()} size="sm" type="button">
              {isBusy ? copy.working : copy.connect}
            </Button>
            <Button disabled={isBusy || !isConnected} onClick={() => void disconnect()} size="sm" type="button" variant="secondary">
              {copy.disconnect}
            </Button>
          </div>
          <p className="text-xs leading-5 text-(--ui-text-tertiary)">{copy.warning}</p>
        </div>
      }
      description={copy.description}
      title={copy.label}
      wide
    />
  )
}
