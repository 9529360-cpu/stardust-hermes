// The Stardust home is named by STARDUST_HOME. HERMES_HOME is the name installs made before the Stardust rename
// export: it is read only when STARDUST_HOME is unset or blank, so an older install's user environment keeps its
// data home. Mirrors configured_home_text() in hermes_constants.py; the Electron main process cannot import the
// Python side. Covered by home-env.test.ts.
export const HOME_ENV_NAMES = ['STARDUST_HOME', 'HERMES_HOME'] as const

// The configured home text from an environment record: STARDUST_HOME, else the legacy HERMES_HOME, else ''.
export function configuredHomeFromEnv(env: Record<string, string | undefined>): string {
  for (const name of HOME_ENV_NAMES) {
    const value = (env[name] ?? '').trim()

    if (value) {
      return value
    }
  }

  return ''
}
