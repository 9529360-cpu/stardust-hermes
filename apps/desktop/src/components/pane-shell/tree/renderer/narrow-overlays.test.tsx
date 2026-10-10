import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { PANE_TOGGLE_REVEAL_EVENT } from '@/components/pane-shell'
import { registry } from '@/contrib/registry'
import { revealPaneFromUser } from '@/store/layout'
import { stubResizeObserver } from '@/test/jsdom'

import { group, split } from '../model'
import { $hiddenTreePanes, $layoutTree, $narrowViewport, declareDefaultTree, revealTreePane } from '../store'

import { NarrowOverlays } from './narrow-overlays'

// Ground truth for "the Bots tab is still visible when the sessions sidebar
// collapses on a narrow window". A collapsible pane DOCKED into the sessions
// zone (SESSIONS | BOTS) must leave the grid with the zone, and the narrow
// edge overlay must mirror the zone's tab strip so the docked pane stays
// reachable — not just the zone's first pane.

beforeAll(() => {
  stubResizeObserver()
})

const disposers: (() => void)[] = []

const registerPane = (id: string, title: string, data: Record<string, unknown>, body: string) => {
  disposers.push(
    registry.register({
      area: 'panes',
      data,
      id,
      render: () => <div data-testid={`${id}-body`}>{body}</div>,
      title
    })
  )
}

beforeEach(() => {
  window.localStorage.clear()
  $hiddenTreePanes.set(new Set())

  registerPane('sessions', 'sessions', { collapsible: true, placement: 'left', width: '237px' }, 'session rows')
  registerPane('bots', 'Bots', { collapsible: true, placement: 'left', width: '260px' }, 'bot roster')
  registerPane('workspace', 'workspace', { placement: 'main', uncloseable: true }, 'chat')

  declareDefaultTree(split('row', [group(['sessions', 'bots']), group(['workspace'])]))
  $narrowViewport.set(true)
})

afterEach(() => {
  cleanup()
  $narrowViewport.set(false)
  $layoutTree.set(null)
  disposers.splice(0).forEach(dispose => dispose())
})

const revealPane = (id: string) => {
  act(() => {
    window.dispatchEvent(new CustomEvent(PANE_TOGGLE_REVEAL_EVENT, { detail: { id, mode: 'open' } }))
  })
}

const overlayTab = (paneId: string) => document.querySelector<HTMLElement>(`[data-narrow-overlay-tab="${paneId}"]`)

describe('narrow overlay of a stacked zone', () => {
  it('mirrors the zone tab strip so every stacked collapsible stays reachable', () => {
    const { getByTestId, queryByTestId } = render(<NarrowOverlays />)

    revealPane('sessions')

    // Both zone-mates surface as tabs; the revealed pane's body is on screen.
    expect(overlayTab('sessions')).toBeTruthy()
    expect(overlayTab('bots')).toBeTruthy()
    expect(getByTestId('sessions-body')).toBeTruthy()
    expect(queryByTestId('bots-body')).toBeNull()

    // Clicking the BOTS tab swaps the overlay to the docked pane.
    fireEvent.pointerDown(overlayTab('bots')!, { button: 0 })
    expect(getByTestId('bots-body')).toBeTruthy()
    expect(queryByTestId('sessions-body')).toBeNull()
  })

  it('keeps the stripless form for a zone with a single collapsible', () => {
    // Direct set: declareDefaultTree only ADOPTS into an existing tree — it
    // would keep the beforeEach zone (with bots) instead of replacing it.
    $layoutTree.set(split('row', [group(['sessions']), group(['workspace'])]))

    const { getByTestId } = render(<NarrowOverlays />)

    revealPane('sessions')

    expect(getByTestId('sessions-body')).toBeTruthy()
    expect(overlayTab('sessions')).toBeNull()
  })

  it('moves the overlay for an explicit app reveal, even when another pane is showing', () => {
    // The app's explicit reveals (the agent-space row, the roster's back control)
    // check the breakpoint before they route into the overlay.
    const matchMedia = window.matchMedia
    window.matchMedia = (query: string) => ({ matches: true, media: query }) as MediaQueryList

    try {
      const { getByTestId, queryByTestId } = render(<NarrowOverlays />)

      revealPane('bots')
      expect(getByTestId('bots-body')).toBeTruthy()

      act(() => {
        revealPaneFromUser('sessions')
      })

      expect(getByTestId('sessions-body')).toBeTruthy()
      expect(queryByTestId('bots-body')).toBeNull()
    } finally {
      window.matchMedia = matchMedia
    }
  })

  it('leaves the overlay closed for a background tree reveal', () => {
    const { queryByTestId } = render(<NarrowOverlays />)

    act(() => {
      revealTreePane('bots')
    })

    expect(queryByTestId('bots-body')).toBeNull()
    expect(queryByTestId('sessions-body')).toBeNull()
  })

  it('opens an explicit reveal of a pane the app had hidden, in the same update', () => {
    // revealTreePane restores a hidden pane before the narrow reveal is dispatched.
    // The overlay has to see the restored pane in that same update, not after
    // React's next render.
    const matchMedia = window.matchMedia
    window.matchMedia = (query: string) => ({ matches: true, media: query }) as MediaQueryList

    try {
      const { getByTestId, queryByTestId } = render(<NarrowOverlays />)

      act(() => {
        $hiddenTreePanes.set(new Set(['bots']))
      })
      expect(queryByTestId('bots-body')).toBeNull()

      act(() => {
        revealPaneFromUser('bots')
      })

      expect(getByTestId('bots-body')).toBeTruthy()
    } finally {
      window.matchMedia = matchMedia
    }
  })
})
