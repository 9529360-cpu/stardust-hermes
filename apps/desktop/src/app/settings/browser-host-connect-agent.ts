import { translateNow } from '@/i18n/runtime'
import { notify, notifyError } from '@/store/notifications'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'

import { connectHostBrowser } from './browser-host-connect'

/**
 * The agent asked (after the backend's approval gate) to pair Chrome to the chat on screen: run the same
 * path as the Connect Chrome button and say so on screen. Failures surface as a toast; the agent reads the
 * outcome from `browser(action="status", target="host")`.
 */
export async function connectHostBrowserForAgent(sessionId: string, chromeProfileDir: string): Promise<void> {
  const profileKey = normalizeProfileKey($activeGatewayProfile.get())

  notify({
    kind: 'info',
    message: translateNow('settings.toolsets.browserHostControl.agentConnectingMessage'),
    title: translateNow('settings.toolsets.browserHostControl.agentConnectingTitle')
  })

  try {
    const started = await connectHostBrowser({ chromeProfileDir, profile: profileKey, profileKey, sessionId })

    if (started?.status === 'connected') {
      notify({
        kind: 'info',
        message: translateNow('settings.toolsets.browserHostControl.connectedMessage'),
        title: translateNow('settings.toolsets.browserHostControl.connectedTitle')
      })
    } else if (started?.status === 'error') {
      // Electron reports a bridge that failed to start as a status, not a rejection.
      notifyError(new Error(started.error || ''), translateNow('settings.toolsets.browserHostControl.connectFailed'))
    }
  } catch (error) {
    notifyError(error, translateNow('settings.toolsets.browserHostControl.connectFailed'))
  }
}
