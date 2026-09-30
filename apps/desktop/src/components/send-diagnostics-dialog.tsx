// Send Diagnostics — consent-gated local debug-bundle export.
//
// Rendered globally (wiring.tsx, beside ConfirmHost) and driven by the
// $sendDiagnostics store. The backend prepares a force-redacted temporary ZIP;
// Electron saves it to the user's machine through the authenticated file bridge;
// the backend copy is discarded after the save attempt. No support-service upload
// is part of this default Stardust flow.
import { useStore } from '@nanostores/react'

import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/copy-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { openExternalLink } from '@/lib/external-link'
import { ExternalLink, Loader2Icon, Lock } from '@/lib/icons'
import { $sendDiagnostics, confirmSendDiagnostics, dismissSendDiagnostics } from '@/store/send-diagnostics'

// Support ownership is Stardust. The saved ZIP is intentionally user-controlled;
// opening the issue tracker is a separate explicit external action.
const SUPPORT_LINKS = [{ key: 'github', url: 'https://github.com/9529360-cpu/stardust-hermes/issues' }] as const

export function SendDiagnosticsHost() {
  const { t } = useI18n()
  const copy = t.sendDiagnostics
  const state = useStore($sendDiagnostics)

  if (!state) {
    return null
  }

  const busy = state.phase === 'preparing'

  return (
    // Dismissal is allowed in EVERY phase, including mid-save: the store's
    // generation guard makes a dismissed save's completion a no-op, so Esc/
    // backdrop/Cancel are always an immediate way out (cancellation of the
    // in-flight request itself stays best-effort).
    <Dialog onOpenChange={open => (!open ? dismissSendDiagnostics() : undefined)} open>
      <DialogContent className="max-w-[30rem]">
        {state.phase === 'consent' || state.phase === 'preparing' ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Lock className="size-4 text-(--ui-text-tertiary)" />
                {copy.title}
              </DialogTitle>
              <DialogDescription className="whitespace-pre-line text-left">{copy.privacyNotice}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button onClick={dismissSendDiagnostics} variant="ghost">
                {copy.cancel}
              </Button>
              <Button disabled={busy} onClick={() => void confirmSendDiagnostics()}>
                {busy ? (
                  <span className="flex items-center gap-1.5">
                    <Loader2Icon className="size-3.5 animate-spin" />
                    {copy.preparing}
                  </span>
                ) : (
                  copy.save
                )}
              </Button>
            </DialogFooter>
          </>
        ) : state.phase === 'error' ? (
          <>
            <DialogHeader>
              <DialogTitle>{copy.failedTitle}</DialogTitle>
              <DialogDescription className="text-left">
                {state.error}
                {'\n'}
                {copy.failedHint}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button onClick={dismissSendDiagnostics} variant="ghost">
                {copy.close}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>{copy.doneTitle}</DialogTitle>
              <DialogDescription className="text-left">{copy.doneDescription}</DialogDescription>
            </DialogHeader>
            {state.result?.savedPath && (
              <div
                className="flex items-center gap-2 rounded-md border border-(--ui-stroke-tertiary) px-3 py-2"
                data-selectable-text="true"
              >
                <code
                  className="min-w-0 flex-1 truncate text-[0.78rem] text-(--ui-text-secondary)"
                  title={state.result.savedPath}
                >
                  {state.result.savedPath}
                </code>
                <CopyButton
                  appearance="inline"
                  className="shrink-0"
                  label={copy.copyPath}
                  text={state.result.savedPath}
                />
              </div>
            )}
            {state.result?.cleanupWarning && (
              <div className="text-[0.78rem] text-(--ui-text-warning)">
                {state.result.cleanupWarning}
              </div>
            )}
            <div className="text-[0.8rem] text-(--ui-text-secondary)">{copy.handoffLead}</div>
            <div className="flex flex-wrap gap-1.5">
              {SUPPORT_LINKS.map(link => (
                <Button key={link.key} onClick={() => openExternalLink(link.url)} size="sm" variant="outline">
                  <ExternalLink className="size-3" />
                  {copy.links[link.key]}
                </Button>
              ))}
            </div>
            <DialogFooter>
              <Button onClick={dismissSendDiagnostics} variant="ghost">
                {copy.close}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
