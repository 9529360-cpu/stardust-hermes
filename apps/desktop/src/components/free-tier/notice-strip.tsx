import { useStore } from '@nanostores/react'
import { useEffect, useId } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { StatusRow } from '@/components/chat/status-row'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { ackFreeTierNotice, claimFreeTierNotice, freeTierNoticeClaim, releaseFreeTierNotice } from '@/store/free-tier'
import { openFreeTierSignIn } from '@/store/free-tier-sign-in'
import { setModelPickerOpen } from '@/store/session'

/**
 * Which mounted composer gets to paint the strip. Several can be on screen at
 * once (split zones, a popout mid-dock); the first to mount claims it and the
 * rest report false, so one pending notice never paints N times. The CALLER
 * asks, so a non-owning stack adds no empty row to its card.
 *
 * Stardust Desktop has retired the inherited Nous free-tier account surface.
 * When the preload bridge is present we therefore decline the claim entirely,
 * even if an older backend still reports a pending free-tier notice.
 */
export function useFreeTierNoticeOwner(): boolean {
  const id = useId()
  const claim = useStore(freeTierNoticeClaim())
  const desktop = typeof window !== 'undefined' && Boolean(window.hermesDesktop)

  useEffect(() => {
    if (desktop) {
      return
    }

    claimFreeTierNotice(id)

    return () => releaseFreeTierNotice(id)
  }, [desktop, id])

  // When the owner unmounts it releases the claim; a composer still mounted takes it over,
  // so the notice does not vanish until some later mount.
  useEffect(() => {
    if (!desktop && claim === null) {
      claimFreeTierNotice(id)
    }
  }, [claim, desktop, id])

  return !desktop && claim === id
}

/**
 * The quiet half of the legacy free-tier introduction. Desktop callers no
 * longer reach this component because `useFreeTierNoticeOwner` declines the
 * claim there. Keep the rendering path temporarily for non-Desktop shared
 * hosts while the compatibility surface is retired separately.
 */
export function FreeTierNoticeStrip() {
  const { requestGateway } = useGatewayRequest()
  const { t } = useI18n()
  const copy = t.freeTier

  const consume = (after?: () => void) => {
    void ackFreeTierNotice(requestGateway)
    after?.()
  }

  return (
    <StatusRow
      leading={<Codicon aria-hidden className="text-(--ui-text-tertiary)" name="account" size="0.8rem" />}
      trailing={
        <>
          <Button
            className="text-foreground/90 hover:text-foreground"
            onClick={() => consume(() => setModelPickerOpen(true))}
            size="micro"
            type="button"
            variant="text"
          >
            {copy.openModelPicker}
          </Button>
          <Button
            className="text-foreground/90 hover:text-foreground"
            onClick={() => consume(() => openFreeTierSignIn())}
            size="micro"
            type="button"
            variant="text"
          >
            {copy.signIn}
          </Button>
          <Button
            className="text-muted-foreground/75 hover:text-foreground/90"
            onClick={() => consume()}
            size="micro"
            type="button"
            variant="text"
          >
            {copy.dismiss}
          </Button>
        </>
      }
      trailingVisible
    >
      <span className="min-w-0 truncate text-[0.73rem] leading-4 text-foreground/92">
        <span className="font-medium">{copy.stripTitle}</span>
        <span className="text-muted-foreground/80"> {copy.stripBody}</span>
      </span>
    </StatusRow>
  )
}
