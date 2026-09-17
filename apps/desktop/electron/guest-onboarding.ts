// Stardust Desktop does not create or advertise the inherited Nous guest/free-tier
// identity. Keep the old launch inputs as compatibility tombstones so stale
// shortcuts or parent environments cannot accidentally reactivate that product
// path while the remaining backend compatibility code is retired separately.

export const GUEST_ONBOARDING_ENV = 'HERMES_GUEST_ONBOARDING'
export const GUEST_ONBOARDING_FLAG = '--guest-onboarding'

// Skip the first-run film. This remains a renderer-only rehearsal aid for any
// onboarding work that does not depend on the retired guest-account path.
export const SKIP_INTRO_ENV = 'HERMES_SKIP_INTRO'
export const SKIP_INTRO_FLAG = '--skip-intro'

/**
 * Compatibility tombstone for the old built-in account switch.
 *
 * Stardust must never mint or surface an implicit Nous identity merely because
 * an inherited environment variable or command-line flag is present. Callers
 * keep using this resolver until the surrounding launch plumbing is deleted,
 * but the product decision is now unconditional: guest onboarding is off.
 */
export function guestOnboardingEnabled(
  _argv: readonly string[] = process.argv,
  _env: NodeJS.ProcessEnv = process.env
): boolean {
  return false
}

export function skipIntroEnabled(
  argv: readonly string[] = process.argv,
  env: NodeJS.ProcessEnv = process.env
): boolean {
  return env[SKIP_INTRO_ENV] === '1' || argv.includes(SKIP_INTRO_FLAG)
}

/**
 * Stamp the retired guest switch off at the process boundary even when the
 * parent environment still contains HERMES_GUEST_ONBOARDING=1. This preserves
 * inherited interface compatibility without allowing it to regain authority.
 */
export function desktopBackendSpawnEnv(base: NodeJS.ProcessEnv, _guestOnboarding: boolean): NodeJS.ProcessEnv {
  return { ...base, [GUEST_ONBOARDING_ENV]: '0' }
}
