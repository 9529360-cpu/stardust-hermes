import { useI18n } from '@/i18n'
import type { Translations } from '@/i18n/types'

export type CorePaneTitleId = keyof Translations['zones']['paneTitles']

/** Localized tab title for a core pane whose registered `title` is a stable id
 *  (`files`, `review`, ...) rather than display copy. */
export function CorePaneTitle({ id }: { id: CorePaneTitleId }) {
  const { t } = useI18n()

  return <>{t.zones.paneTitles[id]}</>
}
