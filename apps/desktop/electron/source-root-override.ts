// Resolves the desktop's source-checkout override, `STARDUST_DESKTOP_HERMES_ROOT`.
//
// The Stardust rename dropped the older `HERMES_DESKTOP_HERMES_ROOT` name with no
// fallback, so a startup file that still set it pointed the desktop at a folder that
// does not exist. The old name is honoured again, but only when the new one is unset,
// and it logs one deprecation warning per process.
import path from 'node:path'

const CURRENT_NAME = 'STARDUST_DESKTOP_HERMES_ROOT'
const LEGACY_NAME = 'HERMES_DESKTOP_HERMES_ROOT'

type EnvLike = Readonly<Record<string, string | undefined>>

let legacyWarningShown = false

/** Absolute source root from the environment, or undefined when no override is set. */
export function resolveSourceRootOverride(
  env: EnvLike = process.env,
  warn: (message: string) => void = console.warn
): string | undefined {
  const current = env[CURRENT_NAME]

  if (current) {
    return path.resolve(current)
  }

  const legacy = env[LEGACY_NAME]

  if (!legacy) {
    return undefined
  }

  if (!legacyWarningShown) {
    legacyWarningShown = true
    warn(`${LEGACY_NAME} is deprecated; set ${CURRENT_NAME} instead.`)
  }

  return path.resolve(legacy)
}

/** Test seam: forget that the deprecation warning already fired in this process. */
export function resetSourceRootOverrideWarningForTests(): void {
  legacyWarningShown = false
}
