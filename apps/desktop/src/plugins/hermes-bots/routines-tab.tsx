/**
 * The Routines pane's tab label, in both shapes a pane tab takes.
 *
 * Expanded, it is the pane's name. Collapsed, the pane sits in the right edge's
 * rail: a 28px column whose words run vertically, and 9px rotated text there is
 * unreadable. The rail shows the calendar glyph instead, with the name as its
 * tooltip and accessible label. The tab shell marks a rail with
 * `data-vertical` (the `group/tab` variant PaneTabLabel already keys its own
 * layout off), so the choice is made here and the shell is left alone.
 */

import { Codicon, Tip, useI18n } from '@hermes/plugin-sdk'

export function RoutinesTabLabel() {
  const { t } = useI18n()
  const label = t.cron.title

  return (
    <>
      <span className="group-data-[vertical]/tab:hidden">{label}</span>
      <Tip label={label}>
        <span
          aria-label={label}
          className="hidden items-center justify-center group-data-[vertical]/tab:flex"
          role="img"
        >
          <Codicon name="calendar" size="0.875rem" />
        </span>
      </Tip>
    </>
  )
}
