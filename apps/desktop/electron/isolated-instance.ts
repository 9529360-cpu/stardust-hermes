import type { QuickEntrySettings } from './quick-entry'

export interface DesktopInstancePolicy {
  readonly isolated: boolean
  /** Run an OS-wide registration only for an ordinary desktop instance. */
  runSystemIntegration<T>(register: () => T): T | undefined
  /** Keep Quick Entry's controller and state honest without acquiring its chord. */
  quickEntrySettings(settings: QuickEntrySettings): QuickEntrySettings
}

/** Explicit instance isolation; unrelated to which client a session connects to. */
export function createDesktopInstancePolicy(env: Record<string, string | undefined>): DesktopInstancePolicy {
  const isolated = env.STARDUST_DESKTOP_ISOLATED === '1'

  return {
    isolated,
    runSystemIntegration(register) {
      if (!isolated) {
        return register()
      }
    },
    quickEntrySettings(settings) {
      return isolated ? { ...settings, enabled: false } : settings
    }
  }
}
