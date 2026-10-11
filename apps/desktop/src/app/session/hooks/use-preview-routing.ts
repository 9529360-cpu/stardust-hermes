import type { GatewayEvent } from '@stardust/shared'
import { useCallback, useEffect, useRef, useState } from 'react'

import { revealTreePane } from '@/components/pane-shell/tree/store'
import { createAutoShowLedger, decideAutoShow } from '@/lib/auto-show'
import { gatewayEventCompletedFileDiff } from '@/lib/gateway-events'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { reachablePreviewUrl } from '@/lib/preview-reach'
import { $autoShowGeneratedFiles } from '@/store/generated-files-auto-show'
import { $rightRailActiveTabId } from '@/store/layout'
import {
  $dockedPreviewTabs,
  $previewTabs,
  beginPreviewServerRestart,
  closePreviewMatching,
  closeRightRail,
  completePreviewServerRestart,
  openPreview,
  progressPreviewServerRestart,
  requestPreviewReload
} from '@/store/preview'
import { $rightContextOpen, setRightContextOpen } from '@/store/right-context'
import { $activeSessionId, $currentCwd } from '@/store/session'
import { $focusedRuntimeId, $sessionStates, $sessionTiles } from '@/store/session-states'

type EventHandler = (event: GatewayEvent) => void

interface PreviewRoutingOptions {
  baseHandleGatewayEvent: EventHandler
  currentCwd: string
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

interface RailVisibility {
  dismissed: Set<string>
  revealed: Set<string>
}

function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
}

function sessionIsOnScreen(sessionId: string): boolean {
  return (
    sessionId === $focusedRuntimeId.get() ||
    sessionId === $activeSessionId.get() ||
    $sessionTiles.get().some(tile => tile.runtimeId === sessionId)
  )
}

/** The workspace a session's files are judged against: its own cwd, never another session's. */
function workspaceOf(sessionId: string | undefined, fallback: string): string {
  if (!sessionId) {
    return ''
  }

  const own = $sessionStates.get()[sessionId]?.cwd?.trim()

  if (own) {
    return own
  }

  return sessionId === $activeSessionId.get() ? $currentCwd.get() || fallback : ''
}

/**
 * Resolve `input` and open it as a rail tab. The rail reveals itself unless the
 * user already hid it this turn; the tab still lands, so it waits there when
 * they look. Nothing here moves focus, so the composer keeps the caret.
 */
async function openInRail(
  input: string,
  workspace: string,
  sessionId: string | undefined,
  label: string,
  visibility: RailVisibility
) {
  const resolved = await normalizeOrLocalPreviewTarget(input, workspace || undefined)

  // Resolution is asynchronous. A user may switch sessions while it runs, and a
  // stale result must not reveal another session's browser or file.
  if (!resolved || (sessionId && !sessionIsOnScreen(sessionId))) {
    return
  }

  const url = resolved.kind === 'url' ? await reachablePreviewUrl(resolved.url) : resolved.url
  // The agent's loopback is the GATEWAY's loopback. Keep the original as the
  // label so the user still sees the address the agent named.
  const reached = url === resolved.url ? resolved : { ...resolved, label: resolved.label || input, url }
  const reveal = !sessionId || !visibility.dismissed.has(sessionId)

  if (reveal && sessionId) {
    visibility.revealed.add(sessionId)
  }

  openPreview(label ? { ...reached, label } : reached, 'tool-result', undefined, reveal)
}

export function usePreviewRouting({ baseHandleGatewayEvent, currentCwd, requestGateway }: PreviewRoutingOptions) {
  // A browser action is foreground intent once per turn. Hiding the viewer is
  // not permission to reopen it after every click; the live page keeps working
  // until a new turn, even if another preview.open navigates the same tab.
  const browserVisibility = useRef<RailVisibility>({ dismissed: new Set<string>(), revealed: new Set<string>() })
  // Which turn is live and which files it already opened (lib/auto-show.ts).
  const [autoShowLedger] = useState(createAutoShowLedger)

  useEffect(
    () =>
      $rightContextOpen.listen(open => {
        if (!open) {
          for (const sessionId of browserVisibility.current.revealed) {
            browserVisibility.current.dismissed.add(sessionId)
          }
        }
      }),
    []
  )

  const restartPreviewServer = useCallback(
    async (url: string, context?: string) => {
      const sessionId = $focusedRuntimeId.get()

      if (!sessionId) {
        throw new Error('No active session for background restart')
      }

      const cwd = $currentCwd.get() || currentCwd || ''

      const result = await requestGateway<{ task_id?: string }>('preview.restart', {
        context: context || undefined,
        cwd: cwd || undefined,
        session_id: sessionId,
        url
      })

      const taskId = result.task_id || ''

      if (!taskId) {
        throw new Error('Background restart did not return a task id')
      }

      beginPreviewServerRestart(taskId, url)

      return taskId
    },
    [currentCwd, requestGateway]
  )

  const handleDesktopGatewayEvent = useCallback<EventHandler>(
    event => {
      baseHandleGatewayEvent(event)

      const sessionId = event.session_id
      if (sessionId && event.type === 'message.start' && !browserVisibility.current.revealed.has(sessionId)) {
        // A session can emit a duplicate start while the same turn is still
        // running. Do not forget a manual dismissal until its completion.
        browserVisibility.current.dismissed.delete(sessionId)
      } else if (sessionId && event.type === 'message.complete') {
        browserVisibility.current.dismissed.delete(sessionId)
        browserVisibility.current.revealed.delete(sessionId)
      }

      if (sessionId && event.type === 'message.start') {
        autoShowLedger.begin(sessionId)
      } else if (sessionId && event.type === 'message.complete') {
        autoShowLedger.end(sessionId)
      }

      // A file the assistant just wrote in a live turn opens by itself, once per
      // turn, on the session the owner is looking at. A completion outside a
      // live turn is a replay, and it never opens anything.
      if (
        event.type === 'tool.complete' &&
        sessionId &&
        sessionIsOnScreen(sessionId) &&
        $autoShowGeneratedFiles.get() &&
        autoShowLedger.isLive(sessionId)
      ) {
        const workspace = workspaceOf(sessionId, currentCwd)
        const decision = decideAutoShow(event, workspace)

        if (decision.open && decision.origin === 'written-file') {
          for (const target of decision.targets) {
            if (target.kind === 'file' && autoShowLedger.claim(sessionId, `file:${target.path}`)) {
              void openInRail(target.path, workspace, sessionId, '', browserVisibility.current)
            }
          }
        }
      }

      if (event.type === 'tool.start' && sessionId && sessionIsOnScreen(sessionId)) {
        const { name, args } = asRecord(event.payload)
        const action = asRecord(args).action
        const browserTarget = asRecord(args).target
        const hasLivePreview = $dockedPreviewTabs.get().some(
          tab =>
            tab.id === $rightRailActiveTabId.get() &&
            (tab.target.kind === 'url' ||
              (tab.target.kind === 'file' && tab.target.previewKind === 'html' && tab.target.renderMode !== 'source'))
        )
        // Only follow tools that drive the ACTUAL in-app guest. browser_exec
        // and browser_navigate own a separate backend Chromium/CDP session:
        // revealing an unrelated webview would be a fake live preview.
        const isLiveBrowserAction =
          (name === 'desktop_preview' && (action === 'open' || (action === 'read' && hasLivePreview))) ||
          ((name === 'drive_preview' || name === 'annotate_preview') && hasLivePreview) ||
          (name === 'browser' &&
            browserTarget === 'in_app' &&
            (action === 'open' || (action !== 'status' && hasLivePreview)))
        if (isLiveBrowserAction && !browserVisibility.current.dismissed.has(sessionId)) {
          browserVisibility.current.revealed.add(sessionId)
          setRightContextOpen(true)
          // The right rail may already be showing Files or Review. Front the
          // active live tab even when the visibility atom was already true.
          if (hasLivePreview && $rightRailActiveTabId.get()) {
            revealTreePane(`preview-tile:${$rightRailActiveTabId.get()}`)
          }
        }
      }

      if (event.type === 'preview.open') {
        // Agent-driven open in response to an explicit user request ("show
        // cnn.com in the preview pane"). Honor it for any session that's ON
        // SCREEN — the primary chat or an open tile — not only the focused
        // one: the turn's window routing already scoped the event to this
        // window, and gating on focus made the open silently vanish whenever
        // the user's click had moved focus to a different zone by the time
        // the tool ran (an "open reddit" they explicitly asked for). A
        // session that is NOT visible anywhere still can't yank the pane
        // open (offer, don't hijack). The auto-show policy decides what may
        // open: a file must sit inside this session's workspace and be a type
        // that is never a program, script, installer, or archive.
        const { label } = asRecord(event.payload)

        if (sessionId && !sessionIsOnScreen(sessionId)) {
          return
        }

        const workspace = workspaceOf(sessionId, currentCwd)
        const decision = decideAutoShow(event, workspace)
        const target = decision.open ? decision.targets[0] : undefined

        // A file opens once per turn. An explicit URL is never deduplicated: the
        // assistant asked for that page, and the open just re-fronts its tab.
        if (
          !target ||
          (target.kind === 'file' && !(sessionId && autoShowLedger.claim(sessionId, `file:${target.path}`)))
        ) {
          return
        }

        void openInRail(
          target.kind === 'url' ? target.url : target.path,
          workspace,
          sessionId,
          typeof label === 'string' ? label.trim() : '',
          browserVisibility.current
        )

        return
      }

      if (event.type === 'preview.close') {
        // Agent-driven close via close_preview. Same on-screen gate as open:
        // a session the user can see may tidy the pane it opened; a hidden
        // background turn must not dismiss the user's preview.
        const { url } = asRecord(event.payload)
        const target = typeof url === 'string' ? url.trim() : ''

        if (event.session_id && !sessionIsOnScreen(event.session_id)) {
          return
        }

        if (!target) {
          closeRightRail()

          return
        }

        if (closePreviewMatching(target)) {
          return
        }

        void normalizeOrLocalPreviewTarget(target, $currentCwd.get() || currentCwd || undefined).then(
          async resolved => {
            const candidates = [target]

            if (resolved) {
              candidates.push(resolved.source, resolved.url)

              if (resolved.kind === 'url') {
                candidates.push(await reachablePreviewUrl(resolved.url))
              }
            }

            closePreviewMatching(...candidates)
          }
        )

        return
      }

      if (event.type === 'preview.restart.complete') {
        const { task_id, text } = asRecord(event.payload)

        if (typeof task_id === 'string' && task_id) {
          completePreviewServerRestart(task_id, typeof text === 'string' ? text : '')
        }
      } else if (event.type === 'preview.restart.progress') {
        const { task_id, text } = asRecord(event.payload)

        if (typeof task_id === 'string' && task_id) {
          progressPreviewServerRestart(task_id, typeof text === 'string' ? text : '')
        }
      }

      if (event.session_id && event.session_id !== $focusedRuntimeId.get()) {
        return
      }

      // Only refresh an already-open live preview when a file changes; never
      // open one unprompted. (Preview links are surfaced from the tool row into
      // the status stack — see tool-fallback.tsx.)
      if ($previewTabs.get().some(tab => tab.target.kind === 'url') && gatewayEventCompletedFileDiff(event)) {
        requestPreviewReload()
      }
    },
    [autoShowLedger, baseHandleGatewayEvent, currentCwd]
  )

  return { handleDesktopGatewayEvent, restartPreviewServer }
}
