import type { BillingBlock } from '@hermes/shared'
import { atom } from 'nanostores'

import { openExternalLink } from '@/lib/external-link'

/**
 * The active inference billing wall, if any. Set from the gateway
 * `message.complete` / `error` event when a turn fails with
 * `FailoverReason.billing` (see `agent/billing_links.py`). One global slot: a
 * credit wall on the active session's provider is the whole app's problem, and
 * the newest block wins. Cleared when a new turn starts or the user dismisses.
 */
export interface ActiveBillingBlock {
  block: BillingBlock
  sessionId: string
  at: number
}

export const $billingBlock = atom<ActiveBillingBlock | null>(null)

/**
 * Navigation intent counter. A toast fired outside React bumps this to ask
 * the shell to open provider setup. The retired built-in Billing/account page
 * is intentionally no longer a recovery target. See `contrib/wiring.tsx`.
 */
export const $billingSettingsRequest = atom(0)

export function setBillingBlock(sessionId: string, block: BillingBlock): void {
  $billingBlock.set({ at: Date.now(), block, sessionId })
}

export function clearBillingBlock(sessionId?: string): void {
  const current = $billingBlock.get()

  if (!current) {
    return
  }

  // A scoped clear (new turn on session X) must not wipe a block raised by a
  // different session's provider.
  if (sessionId && current.sessionId !== sessionId) {
    return
  }

  $billingBlock.set(null)
}

export function requestBillingSettings(): void {
  $billingSettingsRequest.set($billingSettingsRequest.get() + 1)
}

/**
 * The single recovery action for a billing wall, shared by the toast and the
 * in-chat banner so both behave identically: legacy Nous account walls stay
 * inside provider setup, while third-party provider-owned billing URLs may open
 * externally. Missing URLs also fall back to provider setup in Settings.
 */
export function runBillingRecovery(block: BillingBlock): void {
  if (!block.is_nous && block.billing_url) {
    openExternalLink(block.billing_url)

    return
  }

  requestBillingSettings()
}

export function billingCtaLabel(
  block: BillingBlock,
  copy: { addCredits: string; providerSettings: string }
): string {
  return !block.is_nous && block.billing_url ? copy.addCredits : copy.providerSettings
}
