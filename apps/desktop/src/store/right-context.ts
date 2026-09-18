import { Codecs, persistentAtom } from '@/lib/persisted'

const RIGHT_CONTEXT_OPEN_STORAGE_KEY = 'hermes.desktop.rightContextOpen.v1'

/**
 * Product-level visibility for the contextual workspace rail.
 *
 * This is deliberately independent from the Files pane. The private desktop
 * keeps the preview / live-work rail beside the conversation without making it
 * permanent chrome. Fresh conversations start focused on the transcript; an
 * explicit project, preview, Files, Review, or other live-work action opens it.
 */
export const $rightContextOpen = persistentAtom(RIGHT_CONTEXT_OPEN_STORAGE_KEY, false, Codecs.bool)

export function setRightContextOpen(open: boolean): void {
  $rightContextOpen.set(open)
}

export function toggleRightContextOpen(): void {
  setRightContextOpen(!$rightContextOpen.get())
}
