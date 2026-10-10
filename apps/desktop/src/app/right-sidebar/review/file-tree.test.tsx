import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesReviewFile } from '@/global'
import { I18nProvider } from '@/i18n'
import { $sidebarWorkspaceNodeOpen } from '@/store/layout'
import { $reviewFiles, $reviewOpen } from '@/store/review'

import { ReviewFileTree } from './file-tree'

const ROW_HEIGHT = 24
const VIEWPORT_HEIGHT = 600

const file = (path: string, over: Partial<HermesReviewFile> = {}): HermesReviewFile => ({
  added: 1,
  path,
  removed: 0,
  staged: false,
  status: '?',
  ...over
})

// The issue's repro shape: a .NET publish/ folder with tens of thousands of
// untracked files and no .gitignore.
function filesUnderPublish(count: number): HermesReviewFile[] {
  return Array.from({ length: count }, (_, i) => file(`publish/file-${String(i).padStart(4, '0')}.so`))
}

function topLevelFiles(count: number): HermesReviewFile[] {
  return Array.from({ length: count }, (_, i) => file(`file-${String(i).padStart(4, '0')}.ts`))
}

function renderTree() {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <ReviewFileTree />
    </I18nProvider>
  )
}

describe('ReviewFileTree', () => {
  beforeEach(() => {
    $reviewOpen.set(true)
    $reviewFiles.set([])
    $sidebarWorkspaceNodeOpen.set({})

    // jsdom has no layout: report the real row height for virtualized rows and
    // a viewport for the scroller so the virtualizer mounts a deterministic
    // window (instead of measuring everything as 0px).
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      if (this.hasAttribute?.('data-index')) {
        return ROW_HEIGHT
      }

      if (this.hasAttribute?.('data-suppress-pane-reveal-side')) {
        return VIEWPORT_HEIGHT
      }

      return 0
    })
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(() => 240)

    // The virtualizer observes the scroller and rows; jsdom ships no observer,
    // so install a no-op one (initialRect + fixed row heights drive the mount).
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(_callback: ResizeObserverCallback) {}
        disconnect = vi.fn()
        observe = vi.fn()
        unobserve = vi.fn()
      } as unknown as typeof ResizeObserver
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    $reviewFiles.set([])
    $sidebarWorkspaceNodeOpen.set({})
    $reviewOpen.set(false)
  })

  it('virtualizes heavy trees: only the visible window is mounted', () => {
    $reviewFiles.set(topLevelFiles(5000))

    const { container } = renderTree()

    const mounted = container.querySelectorAll('[data-index]')
    expect(mounted.length).toBeGreaterThan(0)
    // 5,000 files would be ~5,000 rows in the DOM without virtualization.
    expect(mounted.length).toBeLessThan(100)

    // The scroller still accounts for the full 5,000 × 24px list height.
    const spacer = container.querySelector<HTMLDivElement>('[style*="120024px"]')
    expect(spacer).not.toBeNull()

    // The window starts at the top, so the first rows are mounted.
    expect(screen.getByText('file-0000.ts')).toBeTruthy()
  })

  it('starts heavy trees collapsed and reveals children on expand, still bounded', () => {
    $reviewFiles.set(filesUnderPublish(5000))

    const { container } = renderTree()

    // Collapsed by default: just the publish/ folder row, no file rows yet.
    expect(screen.getByText('publish')).toBeTruthy()
    expect(container.querySelectorAll('[data-index]').length).toBe(2)

    fireEvent.click(screen.getByText('publish'))

    // Children appear as virtualized rows — a handful, not 5,000.
    const mounted = container.querySelectorAll('[data-index]')
    expect(mounted.length).toBeGreaterThan(2)
    expect(mounted.length).toBeLessThan(100)
    expect(screen.getByText('file-0000.so')).toBeTruthy()
  })

  it('shows staged, unstaged, and untracked sections using the existing status rows', () => {
    $reviewFiles.set([
      file('staged.ts', { added: 2, removed: 1, staged: true, status: 'M' }),
      file('changed.ts', { added: 3, removed: 0, status: 'M' }),
      file('new.ts')
    ])

    const { container } = renderTree()

    expect(screen.getByRole('group', { name: 'Staged' })).toBeTruthy()
    expect(screen.getByRole('group', { name: 'Unstaged' })).toBeTruthy()
    expect(screen.getByRole('group', { name: 'Untracked' })).toBeTruthy()
    expect(container.querySelector('[data-review-section-header="staged"]')?.textContent).toContain('Staged1')
    expect(container.querySelector('[data-review-section-header="unstaged"]')?.textContent).toContain('Unstaged1')
    expect(container.querySelector('[data-review-section-header="untracked"]')?.textContent).toContain('Untracked1')
  })

  it('keeps rendering small trees in full (animated path untouched)', () => {
    $reviewFiles.set([file('a.ts'), file('b.ts'), file('src/c.ts')])

    renderTree()

    expect(screen.getByText('a.ts')).toBeTruthy()
    expect(screen.getByText('b.ts')).toBeTruthy()
    expect(screen.getByText('src')).toBeTruthy()
    expect(screen.getByText('c.ts')).toBeTruthy()
  })
})
