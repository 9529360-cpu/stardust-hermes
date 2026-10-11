import { Codecs, persistentAtom } from '@/lib/persisted'

/**
 * "Show generated files automatically" (Settings → Appearance). When on, a
 * previewable file the assistant writes in the session workspace opens in the
 * right-rail preview by itself (see lib/auto-show.ts for what qualifies).
 *
 * Presentation-only, so the renderer owns it: the backend never reads this, and
 * the decision is made where the gateway events arrive. Default on. The value is
 * written only once the owner changes it.
 */
const KEY = 'hermes.desktop.autoShowGeneratedFiles.v1'

export const $autoShowGeneratedFiles = persistentAtom(KEY, true, Codecs.bool)

export function setAutoShowGeneratedFiles(enabled: boolean): void {
  $autoShowGeneratedFiles.set(enabled)
}
