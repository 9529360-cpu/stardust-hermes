import { cleanup, render, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ComponentProps, ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { type SessionView, SessionViewProvider } from '@/app/chat/session-view'
import { $previewTabs, type PreviewTarget } from '@/store/preview'
import { $previewStatusBySession } from '@/store/preview-status'
import { $activeSessionId, $currentCwd } from '@/store/session'

vi.mock('@assistant-ui/react', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAuiState: (select: (state: unknown) => unknown) =>
    select({ message: { id: 'msg-1', status: { type: 'complete' } }, thread: { isRunning: false } })
}))

const { ToolFallback } = await import('./fallback')

const PRIMARY_ID = 'primary-session'
const TILE_ID = 'tile-session'

/** Minimal tile view: only the fields the tool row reads. */
function tileView(): SessionView {
  return {
    ...({} as SessionView),
    $cwd: atom('/tile/work'),
    $messages: atom([]),
    $runtimeId: atom<null | string>(TILE_ID),
    kind: 'tile'
  }
}

function fileTarget(path: string): PreviewTarget {
  return { kind: 'file', label: path, path, previewKind: /\.pdf$/i.test(path) ? 'pdf' : 'html', source: path, url: `file://${path}` }
}

function renderToolRow(wrap: (node: ReactNode) => ReactNode, path = '/tile/work/report.html', toolName = 'write_file') {
  const props = {
    args: { path },
    result: { path },
    toolCallId: 'call-1',
    toolName
  } as unknown as ComponentProps<typeof ToolFallback>

  render(<>{wrap(<ToolFallback {...props} />)}</>)
}

beforeEach(() => {
  Object.defineProperty(window, 'stardustDesktop', {
    configurable: true,
    value: { normalizePreviewTarget: vi.fn(async (target: string) => fileTarget(target)) }
  })
})

afterEach(() => {
  cleanup()
  $previewStatusBySession.set({})
  $previewTabs.set([])
  $activeSessionId.set(null)
  $currentCwd.set('')
  vi.restoreAllMocks()
})

describe('tool row preview recording', () => {
  // The row used to record under the global (primary-only) $activeSessionId, so
  // a preview produced inside a session TILE surfaced in the main chat's
  // composer instead of the tile's own.
  it('records into the session whose transcript the row is in, not the primary', () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    const view = tileView()

    renderToolRow(node => <SessionViewProvider value={view}>{node}</SessionViewProvider>)

    const recorded = $previewStatusBySession.get()

    expect(Object.keys(recorded)).toEqual([TILE_ID])
    expect(recorded[TILE_ID]?.[0]?.cwd).toBe('/tile/work')
  })

  it('still records into the primary session for the main chat', () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    renderToolRow(node => node)

    expect(Object.keys($previewStatusBySession.get())).toEqual([PRIMARY_ID])
  })
})

// The rail opens by itself only from the gateway's tool.complete event
// (lib/auto-show.ts). The tool row is a render-time view of a finished call, so
// it can only offer a status link; it must never open the rail, or a re-render
// or a replayed transcript would yank the owner's attention.
describe('tool row preview offer', () => {
  it('offers a newly written web page as a status link and leaves the rail alone', async () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    renderToolRow(node => node, '/primary/work/index.html')

    await waitFor(() =>
      expect($previewStatusBySession.get()[PRIMARY_ID]?.map(item => item.target)).toEqual(['/primary/work/index.html'])
    )
    await Promise.resolve()
    expect($previewTabs.get()).toHaveLength(0)
  })

  it('offers a newly written PDF as a status link and leaves the rail alone', async () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    renderToolRow(node => node, '/primary/work/report.pdf')

    await waitFor(() =>
      expect($previewStatusBySession.get()[PRIMARY_ID]?.map(item => item.target)).toEqual(['/primary/work/report.pdf'])
    )
    await Promise.resolve()
    expect($previewTabs.get()).toHaveLength(0)
  })

  it('does not open a preview for plain source output', async () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    renderToolRow(node => node, '/primary/work/main.py')

    // Nothing to await for a "never happens" assertion; give any stray async
    // resolution a turn, then confirm the rail stayed empty.
    await Promise.resolve()
    expect($previewTabs.get()).toHaveLength(0)
    expect(window.stardustDesktop.normalizePreviewTarget).not.toHaveBeenCalled()
  })
})
