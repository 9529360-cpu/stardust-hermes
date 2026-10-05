import type * as React from 'react'

import { cn } from '@/lib/utils'

/** Quiet section heading for side panels: small, sentence case, tertiary text — the label
 *  orders the list without competing with it (no accent color, no tracking, no glyph). */
export function SidebarPanelLabel({ children, className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span
      className={cn(
        'flex min-w-0 items-center pl-2 text-[0.6875rem] font-medium text-(--ui-text-tertiary)',
        className
      )}
      {...props}
    >
      <span className="min-w-0 truncate leading-none">{children}</span>
    </span>
  )
}
