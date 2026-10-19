import './segmented-control.css'

import type { CSSProperties } from 'react'

import type { IconComponent } from '@/lib/icons'
import { cn } from '@/lib/utils'

import { segmentedControlNextIndex } from './segmented-control-navigation'

export interface SegmentedControlOption<T extends string> {
  id: T
  label: string
  icon?: IconComponent
}

type AccessibleName =
  { 'aria-label': string; 'aria-labelledby'?: never } | { 'aria-label'?: never; 'aria-labelledby': string }

type SegmentedControlProps<T extends string> = AccessibleName & {
  options: readonly SegmentedControlOption<T>[]
  value: T
  onChange: (id: T) => void
  className?: string
  /** Dims the whole track and blocks selection (e.g. gated behind a prerequisite). */
  disabled?: boolean
}

/**
 * One controlled choice, one tab stop. The moving surface is purely visual:
 * selection and focus never wait for its transition. Keeps Stardust tokens
 * and button semantics; no foreign theme, global layout id, or animation dependency.
 * Geometry adapted from open-claude-ui; see THIRD_PARTY_NOTICES/open-claude-ui.txt.
 */
export function SegmentedControl<T extends string>({
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
  className,
  disabled = false,
  onChange,
  options,
  value
}: SegmentedControlProps<T>) {
  const activeIndex = options.findIndex(option => option.id === value)
  const tabStop = Math.max(0, activeIndex)

  return (
    <div
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      className={cn(
        'stardust-segmented-control relative isolate inline-grid w-fit min-w-0 max-w-full auto-cols-fr grid-flow-col gap-0.5 rounded-[5px] bg-(--ui-bg-tertiary) p-0.5',
        disabled && 'opacity-50',
        className
      )}
      data-slot="segmented-control"
      role="group"
      style={
        {
          '--segmented-count': Math.max(1, options.length),
          '--segmented-index': Math.max(0, activeIndex)
        } as CSSProperties
      }
    >
      {activeIndex >= 0 && (
        <span
          aria-hidden="true"
          className="stardust-segmented-indicator pointer-events-none absolute rounded-[3px] bg-background shadow-sm"
          data-slot="segmented-control-indicator"
        />
      )}
      {options.map(({ id, label, icon: Icon }, index) => {
        const active = value === id

        return (
          <button
            aria-pressed={active}
            className={cn(
              'relative z-10 flex min-w-0 items-center justify-center gap-1 rounded-[3px] px-2.5 py-0.5 text-[0.6875rem] font-medium transition-colors disabled:cursor-default',
              active ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'
            )}
            data-slot="segmented-control-option"
            disabled={disabled}
            key={id}
            onClick={() => onChange(id)}
            onKeyDown={event => {
              if (disabled || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
                return
              }
              const track = event.currentTarget.parentElement

              if (!track) {
                return
              }
              const rtl = getComputedStyle(track).direction === 'rtl'
              const next = segmentedControlNextIndex(index, options.length, event.key, rtl)

              if (next === null) {
                return
              }

              event.preventDefault()
              event.stopPropagation()
              const buttons = track.querySelectorAll<HTMLButtonElement>('[data-slot="segmented-control-option"]')
              buttons[next]?.focus({ preventScroll: true })

              if (next !== index) {
                onChange(options[next].id)
              }
            }}
            tabIndex={index === tabStop ? 0 : -1}
            type="button"
          >
            {Icon && <Icon aria-hidden="true" className="size-3 shrink-0" />}
            <span className="min-w-0 truncate">{label}</span>
          </button>
        )
      })}
    </div>
  )
}
