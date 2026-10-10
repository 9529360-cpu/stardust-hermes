/**
 * Narrow-viewport edge overlays — the tree's take on the app's hover-reveal
 * collapse. Collapsible panes leave the grid below the sidebar-collapse
 * breakpoint; an edge strip (hover) or PANE_TOGGLE_REVEAL_EVENT (⌘B / ⌘G /
 * titlebar toggles route here on narrow) slides the pane OVER the layout
 * instead of squeezing it. Event reveals pin; hover reveals follow the mouse.
 */

import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'

import { TITLEBAR_HEIGHT } from '@/app/shell/titlebar'
import { $chatOnboardingSolo } from '@/components/onboarding-chat/assembly'
import { PaneTab, PaneTabLabel, PaneTabStrip } from '@/components/ui/pane-tab'
import { ContribBoundary, ContribRender } from '@/contrib/react/boundary'
import { useContributions } from '@/contrib/react/use-contributions'
import { registry } from '@/contrib/registry'
import type { Contribution } from '@/contrib/types'
import { ESCAPE_PRIORITY, isTopEscapeLayer, pushEscapeLayer } from '@/lib/escape-layers'
import { cn } from '@/lib/utils'

import { PANE_TOGGLE_REVEAL_EVENT } from '../..'
import { allPaneIds, findGroupOfPane } from '../model'
import { $hiddenTreePanes, $layoutTree, $narrowViewport } from '../store'

import { usePanelTitlebar } from './panel-titlebar'
import { paneChrome } from './track-model'

/** The panes the edge overlay can show: collapsible, docked in the tree, not hidden. */
function narrowOverlayPanes(
  panes: readonly Contribution[],
  tree: Parameters<typeof allPaneIds>[0] | null,
  hidden: ReadonlySet<string>,
  solo: boolean
): Contribution[] {
  // Solo adopts sidebar panes without their surrounding sidebar chrome.
  // Suppress every reveal path while those panes are intentionally hidden.
  if (solo) {
    return []
  }

  const inTree = new Set(tree ? allPaneIds(tree) : [])

  return panes.filter(p => paneChrome(p).collapsible && inTree.has(p.id) && !hidden.has(p.id))
}

/**
 * The overlay's titlebar band, placed the way the docked zone header is. The
 * window's own clusters (sidebar toggle, right tools, native controls) sit in
 * the top band, so the band reserves their space and the pane body starts
 * below it. The zone's tabs ride in the band beside the toggle when the measured
 * clusters leave room, and drop to a row beneath the band when they don't.
 */
function NarrowOverlayTitlebar({ tabs }: { tabs: ((titlebar: boolean) => ReactNode) | null }) {
  const ref = useRef<HTMLDivElement>(null)
  // The same resolver the docked zone header uses: it measures the clusters.
  const tabsBelowControls = usePanelTitlebar(ref, true, false)
  const tabsInBand = tabs !== null && !tabsBelowControls

  return (
    <>
      <div
        className="relative flex min-w-0 shrink-0 bg-(--ui-sidebar-surface-background)"
        data-narrow-overlay-titlebar=""
        ref={ref}
        style={{ height: TITLEBAR_HEIGHT }}
      >
        <div aria-hidden="true" className="shrink-0" style={{ width: 'var(--panel-titlebar-left, 100%)' }} />
        {tabsInBand ? (
          tabs(true)
        ) : (
          <div
            className="min-w-0 flex-1 self-start [-webkit-app-region:drag]"
            data-window-drag-handle=""
            style={{ height: TITLEBAR_HEIGHT }}
          />
        )}
        <div aria-hidden="true" className="shrink-0" style={{ width: 'var(--panel-titlebar-right, 0px)' }} />
      </div>
      {tabs !== null && tabsBelowControls && tabs(false)}
    </>
  )
}

export function NarrowOverlays() {
  const narrow = useStore($narrowViewport)
  const solo = useStore($chatOnboardingSolo)
  const tree = useStore($layoutTree)
  const panes = useContributions('panes')
  const hiddenPanes = useStore($hiddenTreePanes)
  const [reveal, setReveal] = useState<{ id: string; pinned: boolean } | null>(null)

  // Own an Escape layer only while something is revealed, so Escape closes the
  // overlay only when it's the top layer (never under a dialog / edit mode).
  const revealActive = reveal !== null
  useEffect(() => (revealActive ? pushEscapeLayer(ESCAPE_PRIORITY.narrowOverlay) : undefined), [revealActive])

  const collapsibles = useMemo(
    () => narrowOverlayPanes(panes, tree, hiddenPanes, solo),
    [panes, tree, hiddenPanes, solo]
  )

  // ⌘B / ⌘G's narrow branch dispatches the app's toggle-reveal event with the
  // REAL pane id — accept those via each contribution's revealAliases.
  useEffect(() => {
    if (!narrow || solo) {
      setReveal(null)

      return
    }

    const onToggle = (event: Event) => {
      const detail = (event as CustomEvent<{ id?: string; mode?: 'close' | 'open' | 'toggle' }>).detail
      const id = detail?.id

      if (!id) {
        return
      }

      // Read live state, not the render-time list: a pane that an explicit reveal
      // has just restored is not in that list yet, and the overlay must open it now.
      const live = narrowOverlayPanes(
        registry.getArea('panes'),
        $layoutTree.get(),
        $hiddenTreePanes.get(),
        $chatOnboardingSolo.get()
      )

      const match = live.find(p => p.id === id || paneChrome(p).revealAliases?.includes(id))

      if (!match) {
        return
      }

      // `open`/`close` are explicit intents (programmatic reveal, titlebar show);
      // `toggle` (default) is the ⌘B/⌘G flip.
      const mode = detail?.mode ?? 'toggle'
      setReveal(current => {
        if (mode === 'open') {
          return { id: match.id, pinned: true }
        }

        if (mode === 'close') {
          return current?.id === match.id ? null : current
        }

        return current?.id === match.id && current.pinned ? null : { id: match.id, pinned: true }
      })
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || !isTopEscapeLayer(ESCAPE_PRIORITY.narrowOverlay)) {
        return
      }

      event.preventDefault()
      setReveal(null)
    }

    window.addEventListener(PANE_TOGGLE_REVEAL_EVENT, onToggle)
    window.addEventListener('keydown', onKeyDown)

    return () => {
      window.removeEventListener(PANE_TOGGLE_REVEAL_EVENT, onToggle)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [narrow, solo])

  if (!narrow || solo || collapsibles.length === 0) {
    return null
  }

  const sideOf = (c: Contribution) => (paneChrome(c).placement === 'left' ? 'left' : 'right')
  const revealed = reveal ? collapsibles.find(p => p.id === reveal.id) : undefined
  const sides = [...new Set(collapsibles.map(sideOf))]

  // The revealed pane's ZONE-mates that also left the grid (the sessions zone
  // stacks SESSIONS | BOTS): the overlay mirrors the zone's tab strip so a
  // pane docked into a collapsed zone stays reachable on narrow viewports —
  // without this, only the zone's first pane ever surfaces again.
  const zonePanes = (() => {
    if (!revealed || !tree) {
      return [revealed].filter((p): p is Contribution => Boolean(p))
    }

    const zone = findGroupOfPane(tree, revealed.id)
    const mates = zone ? zone.panes.map(id => collapsibles.find(p => p.id === id)) : []
    const shown = mates.filter((p): p is Contribution => Boolean(p))

    return shown.length > 0 ? shown : [revealed]
  })()

  return (
    <>
      {/* Hover-intent strips on each edge that has a collapsed pane. */}
      {sides.map(side => (
        <div
          className={cn('absolute inset-y-0 z-30 w-1.5', side === 'left' ? 'left-0' : 'right-0')}
          key={side}
          onMouseEnter={() => {
            const first = collapsibles.find(p => sideOf(p) === side)

            if (first) {
              setReveal(current => (current?.pinned ? current : { id: first.id, pinned: false }))
            }
          }}
        />
      ))}

      {revealed && (
        <div
          className={cn(
            'absolute inset-y-0 z-40 flex flex-col overflow-hidden bg-(--ui-sidebar-surface-background) shadow-2xl',
            sideOf(revealed) === 'left'
              ? 'left-0 border-r border-(--ui-stroke-secondary)'
              : 'right-0 border-l border-(--ui-stroke-secondary)'
          )}
          // Floats OVER the layout, so under glass its surface must mask the
          // panes beneath it — a see-through overlay reads as text bleeding
          // through text. Contract: `[data-glass-opaque]` in styles.css.
          data-glass-opaque=""
          data-narrow-overlay=""
          onMouseLeave={() => setReveal(current => (current?.pinned ? current : null))}
          // Match the pane's docked width (sessions ~237px, files its rail
          // width) instead of a fat fixed 20rem — capped for tiny screens.
          style={{ width: `min(${(revealed.data as { width?: string } | undefined)?.width ?? '18rem'}, 85vw)` }}
        >
          {/* Zone-mates share the overlay through the zone's own tab strip
              (SESSIONS | BOTS) — a lone pane keeps the stripless form. The band
              above the body clears the window's titlebar controls. */}
          <NarrowOverlayTitlebar
            tabs={
              zonePanes.length > 1
                ? titlebar => (
                    <PaneTabStrip titlebar={titlebar}>
                      {zonePanes.map(pane => (
                        <PaneTab
                          active={pane.id === revealed.id}
                          aria-selected={pane.id === revealed.id}
                          data-narrow-overlay-tab={pane.id}
                          key={pane.id}
                          onPointerDown={event => {
                            if (event.button === 0) {
                              event.preventDefault()
                              setReveal(current => ({ id: pane.id, pinned: current?.pinned ?? false }))
                            }
                          }}
                        >
                          <PaneTabLabel>{paneChrome(pane).tabTitle?.() ?? pane.title ?? pane.id}</PaneTabLabel>
                        </PaneTab>
                      ))}
                    </PaneTabStrip>
                  )
                : null
            }
          />
          <div className="flex min-h-0 flex-1 flex-col">
            <ContribBoundary id={revealed.id}>
              {revealed.render && <ContribRender render={revealed.render} />}
            </ContribBoundary>
          </div>
        </div>
      )}
    </>
  )
}
