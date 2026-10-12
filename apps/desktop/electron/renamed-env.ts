// Desktop environment variables renamed from HERMES_DESKTOP_* to STARDUST_DESKTOP_*.
//
// The Stardust rename dropped the old names with no fallback, so a shell or startup file that still
// set one was silently ignored. Every renamed name now resolves the same way: the current name wins
// when it is set and non-empty; otherwise the legacy name is honoured, with one deprecation warning
// per name per process. Warnings name the variables only, never their values. The Python backend
// reads the same names through renamed_env.py, which must list the same pairs.
//
// Warnings go to the console and to the desktop log. A warning raised before main.ts installs its
// sink (module-load reads) is held and delivered when the sink is installed.

export const RENAMED_DESKTOP_ENV = {
  STARDUST_DESKTOP_BOOT_FAKE: 'HERMES_DESKTOP_BOOT_FAKE',
  STARDUST_DESKTOP_CHILD_PID: 'HERMES_DESKTOP_CHILD_PID',
  STARDUST_DESKTOP_CWD: 'HERMES_DESKTOP_CWD',
  STARDUST_DESKTOP_DISABLE_GPU: 'HERMES_DESKTOP_DISABLE_GPU',
  STARDUST_DESKTOP_HERMES_ROOT: 'HERMES_DESKTOP_HERMES_ROOT',
  STARDUST_DESKTOP_IGNORE_EXISTING: 'HERMES_DESKTOP_IGNORE_EXISTING',
  STARDUST_DESKTOP_MANAGED: 'HERMES_DESKTOP_MANAGED',
  STARDUST_DESKTOP_PASSWORD_STORE: 'HERMES_DESKTOP_PASSWORD_STORE',
  STARDUST_DESKTOP_READY_FILE: 'HERMES_DESKTOP_READY_FILE',
  STARDUST_DESKTOP_TERMINAL: 'HERMES_DESKTOP_TERMINAL'
} as const

export type RenamedDesktopEnvName = keyof typeof RENAMED_DESKTOP_ENV

export type EnvLike = Readonly<Record<string, string | undefined>>

export type WarningSink = (message: string) => void

const warnedNames = new Set<RenamedDesktopEnvName>()
const heldWarnings: string[] = []
let warningSink: WarningSink | null = null

function emitWarning(message: string, warn: WarningSink | undefined) {
  if (warn) {
    warn(message)
  } else if (warningSink) {
    warningSink(message)
  } else {
    heldWarnings.push(message)
  }
}

/** Where deprecation warnings go when no explicit `warn` is passed. main.ts installs the desktop-log sink. */
export function setRenamedDesktopEnvWarningSink(sink: WarningSink): void {
  warningSink = sink

  for (const message of heldWarnings.splice(0)) {
    sink(message)
  }
}

/** The effective value of a renamed desktop variable, or undefined when neither name is set. */
export function readRenamedDesktopEnv(
  name: RenamedDesktopEnvName,
  env: EnvLike = process.env,
  warn?: WarningSink
): string | undefined {
  const current = env[name]

  if (current) {
    return current
  }

  const legacyName = RENAMED_DESKTOP_ENV[name]
  const legacy = env[legacyName]

  if (!legacy) {
    return undefined
  }

  if (!warnedNames.has(name)) {
    warnedNames.add(name)
    emitWarning(`${legacyName} is deprecated; set ${name} instead.`, warn)
  }

  return legacy
}

/**
 * Reads every renamed name once, so a deprecated name in the environment reaches the desktop log at
 * startup even when only the Python backend reads it.
 */
export function warnAboutDeprecatedDesktopEnv(env: EnvLike = process.env, warn?: WarningSink): void {
  for (const name of Object.keys(RENAMED_DESKTOP_ENV) as RenamedDesktopEnvName[]) {
    readRenamedDesktopEnv(name, env, warn)
  }
}

/** Test seam: forget warnings already emitted, held warnings, and any installed sink. */
export function resetRenamedDesktopEnvForTests(): void {
  warnedNames.clear()
  heldWarnings.splice(0)
  warningSink = null
}
