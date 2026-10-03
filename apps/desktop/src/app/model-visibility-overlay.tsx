import { useStore } from '@nanostores/react'

import { ModelVisibilityDialog } from '@/components/model-visibility-dialog'
import type { HermesGateway } from '@/hermes'
import { $modelVisibilityOpen, setModelVisibilityOpen } from '@/store/model-visibility'
import { $activeSessionId, $gatewayState } from '@/store/session'

interface ModelVisibilityOverlayProps {
  gateway?: HermesGateway
  onAddModelApi: () => void
  ownerConnectionId?: string
  profile: string
}

export function ModelVisibilityOverlay({
  gateway,
  onAddModelApi,
  ownerConnectionId,
  profile
}: ModelVisibilityOverlayProps) {
  const activeSessionId = useStore($activeSessionId)
  const gatewayOpen = useStore($gatewayState) === 'open'
  const open = useStore($modelVisibilityOpen)

  if (!gatewayOpen) {
    return null
  }

  return (
    <ModelVisibilityDialog
      gw={gateway}
      onAddModelApi={onAddModelApi}
      onOpenChange={setModelVisibilityOpen}
      open={open}
      ownerConnectionId={ownerConnectionId}
      profile={profile}
      sessionId={activeSessionId}
    />
  )
}
