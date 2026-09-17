/** Guided onboarding has its own UI-only launch flag; it must never depend
 * on the retired built-in Nous/guest account switch. */
export function isOnboardingEnabled(): boolean {
  return window.hermesDesktop?.guidedOnboardingEnabled === true
}
