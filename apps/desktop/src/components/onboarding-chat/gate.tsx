import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { takeGuideShape } from '@/components/onboarding-chat/assembly'
import { isOnboardingEnabled } from '@/lib/onboarding-enabled'
import { $introReveal } from '@/store/intro-reveal'
import { $desktopOnboarding, $desktopRuntimeVerified } from '@/store/onboarding'
import { $onboardingGate, runGuideKickoff } from '@/store/onboarding-gate'

interface OnboardingChatGateProps {
  enabled: boolean
  onKickoff: () => Promise<boolean>
}

export function OnboardingChatGate({ enabled, onKickoff }: OnboardingChatGateProps) {
  const gate = useStore($onboardingGate)
  const intro = useStore($introReveal)
  const onboarding = useStore($desktopOnboarding)
  const runtimeVerified = useStore($desktopRuntimeVerified)
  const providerReady = onboarding.configured === true && runtimeVerified

  // A queued guide may outlive a relaunch, but it may not steal the shell until
  // the selected provider is actually usable. Fresh users configure a provider
  // first; once that succeeds, the same queued guide resumes without losing its phase.
  useEffect(() => {
    if (enabled && providerReady && gate.guideQueued && intro.phase === 'hidden') {
      takeGuideShape()
    }
  }, [enabled, gate.guideQueued, intro.phase, providerReady])

  useEffect(() => {
    if (
      enabled &&
      providerReady &&
      isOnboardingEnabled() &&
      gate.guideQueued &&
      intro.phase === 'hidden'
    ) {
      void runGuideKickoff(onKickoff)
    }
  }, [enabled, gate.guideQueued, intro.phase, onKickoff, providerReady])

  return null
}
