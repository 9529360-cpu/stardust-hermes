// The desktop's source-checkout override. `STARDUST_DESKTOP_HERMES_ROOT` points the app at a
// developer checkout. It is read through renamed-env.ts, which also honours the legacy
// `HERMES_DESKTOP_HERMES_ROOT` name when the current one is unset, with one deprecation warning per
// process. After the rename, a startup file that still set the legacy name was silently ignored, so
// the desktop did not run from the checkout that file named.
import path from 'node:path'

import { type EnvLike, readRenamedDesktopEnv, type WarningSink } from './renamed-env'

/** Absolute source root from the environment, or undefined when no override is set. */
export function resolveSourceRootOverride(env: EnvLike = process.env, warn?: WarningSink): string | undefined {
  const value = readRenamedDesktopEnv('STARDUST_DESKTOP_HERMES_ROOT', env, warn)

  return value ? path.resolve(value) : undefined
}
