import type { DiffHunk } from '@/components/chat/diff-lines'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'

export interface ReviewHunkNavigatorProps {
  allHunks: string
  count: number
  hunkLabel: (current: number, total: number) => string
  nextHunk: string
  onSelect: (index: number | null) => void
  previousHunk: string
  selectedIndex: number | null
}

/**
 * A local-only hunk focus control for the review diff. It deliberately emits a
 * selected index rather than a git operation: the existing file-scoped review
 * state remains authoritative, and no backend persistence is implied.
 */
export function ReviewHunkNavigator({
  allHunks,
  count,
  hunkLabel,
  nextHunk,
  onSelect,
  previousHunk,
  selectedIndex
}: ReviewHunkNavigatorProps) {
  if (count < 2) {
    return null
  }

  const move = (direction: -1 | 1) => {
    const current = selectedIndex == null ? (direction === 1 ? -1 : 0) : selectedIndex
    onSelect((current + direction + count) % count)
  }

  return (
    <div aria-label={allHunks} className="ml-1 flex shrink-0 items-center gap-0.5" data-slot="review-hunk-navigation">
      <Button aria-label={previousHunk} className="size-5" onClick={() => move(-1)} size="icon-xs" variant="ghost">
        <Codicon name="chevron-up" size="0.75rem" />
      </Button>
      <span aria-live="polite" className="max-w-[7rem] truncate px-1 text-[0.62rem] text-(--ui-text-tertiary)">
        {selectedIndex == null ? allHunks : hunkLabel(selectedIndex + 1, count)}
      </span>
      <Button aria-label={nextHunk} className="size-5" onClick={() => move(1)} size="icon-xs" variant="ghost">
        <Codicon name="chevron-down" size="0.75rem" />
      </Button>
      {selectedIndex != null && (
        <Button aria-label={allHunks} className="size-5" onClick={() => onSelect(null)} size="icon-xs" variant="ghost">
          <Codicon name="list-flat" size="0.75rem" />
        </Button>
      )}
    </div>
  )
}

export function diffForSelectedHunk(diff: string, hunks: readonly DiffHunk[], selectedIndex: number | null): string {
  return selectedIndex == null ? diff : hunks[selectedIndex]?.diff ?? diff
}
