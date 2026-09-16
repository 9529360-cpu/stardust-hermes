import { Codecs, persistentAtom } from '@/lib/persisted'

const RIGHT_CONTEXT_OPEN_STORAGE_KEY = 'hermes.desktop.rightContextOpen.v1'

/**
 * Product-level visibility for the contextual workspace rail.
 *
 * This is deliberately independent from the Files pane. The private desktop
 * keeps its workspace overview visible by default while Files / Review remain
 * opt-in tools inside that rail.
 */
export const $rightContextOpen = persistentAtom(RIGHT_CONTEXT_OPEN_STORAGE_KEY, true, Codecs.bool)

export function setRightContextOpen(open: boolean): void {
  $rightContextOpen.set(open)
}

export function toggleRightContextOpen(): void {
  setRightContextOpen(!$rightContextOpen.get())
}
