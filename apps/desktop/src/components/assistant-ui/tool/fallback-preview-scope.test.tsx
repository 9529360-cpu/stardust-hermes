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
  Object.defineProperty(window, 'hermesDesktop', {
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

describe('tool row preview auto-open', () => {
  it('auto-opens a newly written web page', async () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    renderToolRow(node => node, '/primary/work/index.html')

    await waitFor(() => expect($previewTabs.get()).toHaveLength(1))
    expect($previewTabs.get()[0]?.target.path).toBe('/primary/work/index.html')
  })

  it('auto-opens a newly written PDF', async () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    renderToolRow(node => node, '/primary/work/report.pdf')

    await waitFor(() => expect($previewTabs.get()).toHaveLength(1))
    expect($previewTabs.get()[0]?.target.path).toBe('/primary/work/report.pdf')
  })

  it('does not open a preview for plain source output', async () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    renderToolRow(node => node, '/primary/work/main.py')

    // Nothing to await for a "never happens" assertion; give any stray async
    // resolution a turn, then confirm the rail stayed empty.
    await Promise.resolve()
    expect($previewTabs.get()).toHaveLength(0)
    expect(window.hermesDesktop.normalizePreviewTarget).not.toHaveBeenCalled()
  })

  it('does not re-open after the user closes it and the same tool row re-renders', async () => {
    $activeSessionId.set(PRIMARY_ID)
    $currentCwd.set('/primary/work')

    const { rerender } = render(<ToolFallback {...({ args: { path: '/primary/work/index.html' }, result: { path: '/primary/work/index.html' }, toolCallId: 'call-1', toolName: 'write_file' } as unknown as ComponentProps<typeof ToolFallback>)} />)

    await waitFor(() => expect($previewTabs.get()).toHaveLength(1))
    $previewTabs.set([])

    rerender(
      <ToolFallback
        {...({
          args: { path: '/primary/work/index.html' },
          result: { path: '/primary/work/index.html' },
          toolCallId: 'call-1',
          toolName: 'write_file'
        } as unknown as ComponentProps<typeof ToolFallback>)}
      />
    )

    await Promise.resolve()
    expect($previewTabs.get()).toHaveLength(0)
  })
})
