import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DiffHunk } from '@/components/chat/diff-lines'

import { diffForSelectedHunk, ReviewHunkNavigator } from './hunk-navigation'

afterEach(cleanup)

const hunks: DiffHunk[] = [
  { header: '@@ -1 +1 @@', diff: '@@ -1 +1 @@\n-old\n+first' },
  { header: '@@ -10 +10 @@', diff: '@@ -10 +10 @@\n-old\n+second' },
  { header: '@@ -20 +20 @@', diff: '@@ -20 +20 @@\n-old\n+third' }
]

const labels = {
  allHunks: 'All hunks',
  count: hunks.length,
  hunkLabel: (current: number, total: number) => `Hunk ${current} of ${total}`,
  nextHunk: 'Next hunk',
  previousHunk: 'Previous hunk'
}

describe('ReviewHunkNavigator', () => {
  it('cycles through hunk selection and exposes the all-hunks view again', () => {
    const onSelect = vi.fn()
    const { rerender } = render(<ReviewHunkNavigator {...labels} onSelect={onSelect} selectedIndex={null} />)

    fireEvent.click(screen.getByRole('button', { name: 'Next hunk' }))
    expect(onSelect).toHaveBeenLastCalledWith(0)

    rerender(<ReviewHunkNavigator {...labels} onSelect={onSelect} selectedIndex={0} />)
    expect(screen.getByText('Hunk 1 of 3')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Previous hunk' }))
    expect(onSelect).toHaveBeenLastCalledWith(2)

    rerender(<ReviewHunkNavigator {...labels} onSelect={onSelect} selectedIndex={2} />)
    fireEvent.click(screen.getByRole('button', { name: 'All hunks' }))
    expect(onSelect).toHaveBeenLastCalledWith(null)
  })

  it('does not render navigation for fewer than two hunks', () => {
    const { container } = render(
      <ReviewHunkNavigator {...labels} count={1} onSelect={vi.fn()} selectedIndex={null} />
    )

    expect(container.querySelector('[data-slot="review-hunk-navigation"]')).toBeNull()
  })
})

describe('diffForSelectedHunk', () => {
  it('keeps the full diff unless one hunk is explicitly selected', () => {
    const fullDiff = hunks.map(hunk => hunk.diff).join('\n')

    expect(diffForSelectedHunk(fullDiff, hunks, null)).toBe(fullDiff)
    expect(diffForSelectedHunk(fullDiff, hunks, 1)).toBe(hunks[1].diff)
  })
})
