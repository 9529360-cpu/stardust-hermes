import { type ProfileScope, saveHermesConfigRecord } from '@/hermes'
import { requestGatewayForProfile } from '@/store/gateway'

export const PLAYWRIGHT_MCP_PACKAGE = '@playwright/mcp@0.0.83'
export const DEFAULT_CHROME_PROFILE_DIR = 'Default'

export const BROWSER_CONTROL_CAPABILITIES = [
  'browser_navigate',
  'browser_snapshot',
  'browser_click',
  'browser_type',
  'browser_scroll',
  'browser_press',
  'browser_back',
  'browser_tabs'
]

export type BridgeStatus = 'inactive' | 'starting' | 'connected' | 'stopping' | 'error'

export type BrowserBridgeStatus = {
  state?: BridgeStatus | 'stopped'
  status?: BridgeStatus
  error?: string
  session_id?: string
  controller_id?: string
  browser_profile_id?: string
  capabilities?: string[]
}

/** Chrome profile directory names only ("Default", "Profile 2"), never paths. */
export function isChromeProfileDir(value: string): boolean {
  return /^[\w .-]{1,90}$/.test(value)
}

export class InvalidChromeProfileError extends Error {
  constructor(readonly profileDir: string) {
    super(`Not a Chrome profile directory name: ${profileDir}`)
    this.name = 'InvalidChromeProfileError'
  }
}

/**
 * Pair the user's existing Chrome to one chat: the backend mints a session-bound one-time grant and
 * Electron starts the bridge with it. Shared by the Tools page's Connect Chrome button and the agent's
 * `browser(action="connect", target="host")` request, so both take exactly the same path.
 */
export async function connectHostBrowser({
  chromeProfileDir,
  profile,
  profileKey,
  sessionId
}: {
  chromeProfileDir: string
  profile?: ProfileScope
  profileKey: string
  sessionId: string
}): Promise<BrowserBridgeStatus> {
  const profileDir = chromeProfileDir.trim() || DEFAULT_CHROME_PROFILE_DIR

  if (!isChromeProfileDir(profileDir)) {
    throw new InvalidChromeProfileError(profileDir)
  }

  const browserProfileId = `chrome-${btoa(profileDir).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')}`
  await saveHermesConfigRecord({ browser: { extension_control: { enabled: true } } }, profile)

  const prepared = await requestGatewayForProfile<Record<string, unknown>>(
    profileKey,
    'browser.controller.bridge_prepare',
    {
      session_id: sessionId,
      browser_profile_id: browserProfileId,
      capabilities: BROWSER_CONTROL_CAPABILITIES,
      protocol_version: 1
    }
  )

  return window.stardustDesktop.browserControl.start({
    profile: profileKey,
    launchContext: prepared?.launch_context || prepared,
    chromeProfileDir: profileDir,
    packageSpec: PLAYWRIGHT_MCP_PACKAGE
  })
}

function bridgeState(status: BrowserBridgeStatus | null | undefined): BridgeStatus | 'stopped' | undefined {
  return status?.status || status?.state
}

/**
 * The panel's view of this chat's bridge. The backend knows a bridge only once it has registered, while
 * Electron owns the child from the moment it starts: a panel opened during those first seconds (the
 * agent's connect makes that likely) would otherwise read "inactive" and never look again.
 */
export function reconcileBridgeStatus(
  backend: BrowserBridgeStatus | null | undefined,
  local: BrowserBridgeStatus | null | undefined,
  sessionId: string
): BrowserBridgeStatus {
  const backendState = bridgeState(backend)
  const localState = bridgeState(local)
  const localIsThisChat = Boolean(local?.session_id) && local?.session_id === sessionId

  if (backendState !== 'connected' && localIsThisChat && (localState === 'starting' || localState === 'connected')) {
    return local as BrowserBridgeStatus
  }

  return backend || { state: 'inactive', status: 'inactive' }
}
