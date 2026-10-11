import { useStore } from '@nanostores/react'

import { cn } from '@/lib/utils'
import { $backdropOpacity, BACKDROP_OPACITY_MAX, BACKDROP_OPACITY_MIN, setBackdropOpacity } from '@/store/backdrop'

/** The opacity of the custom window backdrop image, as a percent slider. Shared by the quick panel and Appearance settings. */
export function BackdropOpacitySlider({ className, label }: { className?: string; label: string }) {
  const opacity = useStore($backdropOpacity)

  return (
    <div className={cn('flex min-w-0 items-center gap-3', className)}>
      <span className="shrink-0 text-xs text-(--ui-text-tertiary)">{label}</span>
      <input
        aria-label={label}
        className="h-1 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-(--ui-stroke-tertiary)"
        max={BACKDROP_OPACITY_MAX}
        min={BACKDROP_OPACITY_MIN}
        onChange={event => setBackdropOpacity(Number(event.target.value))}
        step={5}
        style={{ accentColor: 'var(--dt-primary)' }}
        type="range"
        value={opacity}
      />
      <span className="w-9 shrink-0 text-right text-xs tabular-nums text-(--ui-text-tertiary)">{opacity}%</span>
    </div>
  )
}
