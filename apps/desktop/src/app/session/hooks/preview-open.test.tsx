import type { GatewayEvent } from '@stardust/shared'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { assistantTextPart, type ChatMessage } from '@/lib/chat-messages'
import { $autoShowGeneratedFiles, setAutoShowGeneratedFiles } from '@/store/generated-files-auto-show'
import { $previewTabs, $previewTarget, closeRightRail, openPreview, type PreviewTarget } from '@/store/preview'
import { $rightContextOpen, setRightContextOpen } from '@/store/right-context'
import { $activeSessionId, $currentCwd, $messages, $selectedStoredSessionId } from '@/store/session'
import { $sessionStates, $sessionTiles } from '@/store/session-states'

import { usePreviewRouting } from './use-preview-routing'

const RUNTIME_SESSION_ID = '20260727_140707_edec2d'
const WORKSPACE = '/work'

function assistantMessage(id: string, text: string): ChatMessage {
  return { id, parts: [assistantTextPart(text)], role: 'assistant' }
}

function fileTarget(path: string): PreviewTarget {
  return { kind: 'file', label: path, path, previewKind: 'html', source: path, url: `file://${path}` }
}

let handleEvent: (event: GatewayEvent) => void = () => undefined

function Harness() {
  const routing = usePreviewRouting({
    baseHandleGatewayEvent: vi.fn(),
    currentCwd: WORKSPACE,
    requestGateway: vi.fn()
  })

  useEffect(() => {
    handleEvent = routing.handleDesktopGatewayEvent
  }, [routing.handleDesktopGatewayEvent])

  return null
}

async function emitPreviewOpen(url = `${WORKSPACE}/artifact-test.html`, sessionId = RUNTIME_SESSION_ID) {
  await act(async () => {
    handleEvent({
      payload: { label: 'hi bestie', url },
      session_id: sessionId,
      type: 'preview.open'
    } as unknown as GatewayEvent)
  })
}

async function emitToolStart(name: string, args: Record<string, unknown> = {}, sessionId = RUNTIME_SESSION_ID) {
  await act(async () => {
    handleEvent({
      payload: { args, name, tool_id: 'call-browser' },
      session_id: sessionId,
      type: 'tool.start'
    } as unknown as GatewayEvent)
  })
}

async function emitToolComplete(
  name: string,
  result: unknown,
  args: Record<string, unknown> = {},
  sessionId = RUNTIME_SESSION_ID
) {
  await act(async () => {
    handleEvent({
      payload: { args, name, result, tool_id: 'call-write' },
      session_id: sessionId,
      type: 'tool.complete'
    } as unknown as GatewayEvent)
  })
}

async function emitTurn(type: 'message.complete' | 'message.start', sessionId = RUNTIME_SESSION_ID) {
  await act(async () => {
    handleEvent({ payload: {}, session_id: sessionId, type } as unknown as GatewayEvent)
  })
}

async function emitPreviewClose(url?: string, sessionId = RUNTIME_SESSION_ID) {
  await act(async () => {
    handleEvent({
      payload: url === undefined ? {} : { url },
      session_id: sessionId,
      type: 'preview.close'
    } as unknown as GatewayEvent)
  })
}

/** Let any async rail resolution finish, so a "never opens" assertion means something. */
async function settle() {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 10))
  })
}

describe('preview routing', () => {
  beforeEach(() => {
    // A live session always has a runtime id; only the STORED id lags.
    $activeSessionId.set(RUNTIME_SESSION_ID)
    $currentCwd.set(WORKSPACE)
    $messages.set([])
    $sessionStates.set({ [RUNTIME_SESSION_ID]: { cwd: WORKSPACE } as never })
    closeRightRail()
    setRightContextOpen(false)
    setAutoShowGeneratedFiles(true)
    window.localStorage.clear()

    Object.defineProperty(window, 'stardustDesktop', {
      configurable: true,
      value: { normalizePreviewTarget: vi.fn(async (target: string) => fileTarget(target)) }
    })
  })

  afterEach(() => {
    cleanup()
    $messages.set([])
    $sessionStates.set({})
    closeRightRail()
    setRightContextOpen(false)
    setAutoShowGeneratedFiles(true)
    $activeSessionId.set(null)
    $selectedStoredSessionId.set(null)
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  describe('open_preview', () => {
    // The rail used to hold a session-keyed singleton alongside its tabs, written
    // under one session-id rule and reconciled under another. A live session with
    // no stored id yet resolved to '' on the write side, so the target was set and
    // then immediately reconciled back to null — the pane flashed and vanished.
    it('opens a tab for a session that has no stored id yet', async () => {
      $selectedStoredSessionId.set(null)
      render(<Harness />)

      await emitPreviewOpen()

      await waitFor(() => expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/artifact-test.html`))
      expect($previewTabs.get()).toHaveLength(1)
    })

    it('keeps the tab when the stored session id arrives afterwards', async () => {
      $selectedStoredSessionId.set(null)
      render(<Harness />)

      await emitPreviewOpen()
      await waitFor(() => expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/artifact-test.html`))

      await act(async () => {
        $selectedStoredSessionId.set('stored-1')
      })

      expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/artifact-test.html`)
    })

    it('ignores an open from a session that is not the one on screen', async () => {
      render(<Harness />)

      await act(async () => {
        handleEvent({
          payload: { url: `${WORKSPACE}/other.html` },
          session_id: 'some-other-session',
          type: 'preview.open'
        } as unknown as GatewayEvent)
      })

      expect($previewTabs.get()).toHaveLength(0)
    })

    // The turn that calls open_preview is often a TILE's session while focus
    // sits on main (the user asked, then clicked elsewhere). On-screen is the
    // bar — gating on focus made an explicit "open reddit" silently vanish.
    it('honors an open from an open tile session even when main holds focus', async () => {
      const tiles = $sessionTiles.get()

      $sessionTiles.set([{ dir: 'right', runtimeId: 'tile-runtime', storedSessionId: 'stored-tile' }])
      $sessionStates.set({ 'tile-runtime': { cwd: WORKSPACE } as never })
      render(<Harness />)

      try {
        await emitPreviewOpen(`${WORKSPACE}/from-tile.html`, 'tile-runtime')

        await waitFor(() => expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/from-tile.html`))
      } finally {
        $sessionTiles.set(tiles)
      }
    })

    it('opens a second target as its own tab rather than replacing the first', async () => {
      render(<Harness />)

      await emitPreviewOpen(`${WORKSPACE}/one.html`)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(1))

      await emitPreviewOpen(`${WORKSPACE}/two.html`)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(2))

      expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/two.html`)
    })

    it('re-fronts the existing tab when the same target opens twice', async () => {
      render(<Harness />)

      await emitPreviewOpen(`${WORKSPACE}/one.html`)
      await emitPreviewOpen(`${WORKSPACE}/one.html`)

      await waitFor(() => expect($previewTabs.get()).toHaveLength(1))
    })

    it('renders a tool-opened html file rather than showing its source', async () => {
      render(<Harness />)

      await emitPreviewOpen()

      await waitFor(() => expect($previewTarget.get()?.renderMode).toBe('preview'))
    })

    it('refuses an explicit open of a file outside the session workspace', async () => {
      render(<Harness />)

      await emitPreviewOpen('/tmp/artifact-test.html')
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
      expect($rightContextOpen.get()).toBe(false)
    })

    it('never opens a program or script by explicit request', async () => {
      render(<Harness />)

      await emitPreviewOpen(`${WORKSPACE}/installer.exe`)
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
    })

    // Offer, don't hijack: only an explicit open_preview call opens the rail.
    it('does not infer a preview from assistant prose', async () => {
      render(<Harness />)

      await act(async () => {
        $messages.set([
          assistantMessage('a1', 'Preview: http://localhost:5173/'),
          assistantMessage('a2', 'Open /work/demo.html')
        ])
      })

      expect($previewTabs.get()).toHaveLength(0)
      expect(window.stardustDesktop.normalizePreviewTarget).not.toHaveBeenCalled()
    })

    it('does not open a preview off the back of a tool result', async () => {
      render(<Harness />)

      await act(async () => {
        handleEvent({
          payload: { inline_diff: 'a/preview-demo.html -> b/preview-demo.html\n' },
          session_id: RUNTIME_SESSION_ID,
          type: 'tool.complete'
        } as unknown as GatewayEvent)
        handleEvent({
          payload: { path: './dist/index.html' },
          session_id: RUNTIME_SESSION_ID,
          type: 'tool.complete'
        } as unknown as GatewayEvent)
      })

      expect($previewTabs.get()).toHaveLength(0)
    })
  })

  describe('automatic browser viewer', () => {
    it('auto-reveals unified in-app Browser work and honors a manual dismissal', async () => {
      render(<Harness />)
      await emitToolStart('browser', { action: 'open', target: 'in_app', url: 'https://example.com' })
      expect($rightContextOpen.get()).toBe(true)
      expect($previewTabs.get()).toHaveLength(0)

      await emitPreviewOpen(`${WORKSPACE}/browser-page.html`)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(1))
      const liveTabs = $previewTabs.get()
      setRightContextOpen(false)

      await emitToolStart('browser', { action: 'elements', target: 'in_app' })
      expect($rightContextOpen.get()).toBe(false)
      expect($previewTabs.get()).toBe(liveTabs)

      await act(async () => {
        handleEvent({ session_id: RUNTIME_SESSION_ID, type: 'message.complete', payload: {} } as GatewayEvent)
        handleEvent({ session_id: RUNTIME_SESSION_ID, type: 'message.start', payload: {} } as GatewayEvent)
      })
      await emitToolStart('browser', { action: 'elements', target: 'in_app' })
      expect($rightContextOpen.get()).toBe(true)
    })

    it('never opens an unrelated right-side WebView for a host browser action', async () => {
      render(<Harness />)
      await emitToolStart('browser', { action: 'open', target: 'host', url: 'https://example.com' })
      await emitToolStart('browser', { action: 'click', target: 'host', ref: '@e1' })
      await emitToolStart('browser', { action: 'status', target: 'in_app' })
      expect($rightContextOpen.get()).toBe(false)
      expect($previewTabs.get()).toHaveLength(0)
    })

    it('reveals the existing in-app browser when foreground interaction begins, without another tab', async () => {
      render(<Harness />)
      openPreview({ kind: 'url', label: 'Example', source: 'https://example.com', url: 'https://example.com' })
      const existing = $previewTabs.get()
      setRightContextOpen(false)

      await emitToolStart('annotate_preview', { action: 'pin' })
      await emitToolStart('drive_preview', { action: 'elements' })

      expect($rightContextOpen.get()).toBe(true)
      expect($previewTabs.get()).toBe(existing)
      expect($previewTabs.get()).toHaveLength(1)
    })

    it('automatically reveals a browser about to open, but not an unrelated tool', async () => {
      render(<Harness />)
      await emitToolStart('terminal', { command: 'ls' })
      expect($rightContextOpen.get()).toBe(false)

      await emitToolStart('desktop_preview', { action: 'open', url: 'https://example.com' })
      expect($rightContextOpen.get()).toBe(true)
      expect($previewTabs.get()).toHaveLength(0)
    })

    it('does not re-open the viewer after the user hides it during a browser task', async () => {
      render(<Harness />)
      await emitToolStart('desktop_preview', { action: 'open', url: `${WORKSPACE}/one.html` })
      expect($rightContextOpen.get()).toBe(true)

      setRightContextOpen(false)
      // Duplicate turn-start announcements must not erase the viewer dismissal.
      await act(async () => {
        handleEvent({ session_id: RUNTIME_SESSION_ID, type: 'message.start', payload: {} } as GatewayEvent)
      })
      await emitToolStart('desktop_preview', { action: 'open', url: `${WORKSPACE}/two.html` })
      await emitPreviewOpen(`${WORKSPACE}/two.html`)

      await waitFor(() => expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/two.html`))
      expect($rightContextOpen.get()).toBe(false)
      expect($previewTabs.get()).toHaveLength(1)

      await act(async () => {
        handleEvent({ session_id: RUNTIME_SESSION_ID, type: 'message.complete', payload: { text: 'done' } } as GatewayEvent)
        handleEvent({ session_id: RUNTIME_SESSION_ID, type: 'message.start', payload: {} } as GatewayEvent)
      })
      await emitToolStart('drive_preview', { action: 'elements' })
      // The current tab is an HTML preview and the next turn may show it again.
      expect($rightContextOpen.get()).toBe(true)
    })

    it('never opens the foreground for a background session or a separate CDP browser', async () => {
      render(<Harness />)
      await emitToolStart('desktop_preview', { action: 'open' }, 'off-screen-session')
      await emitToolStart('browser_exec', { code: 'print(page_info())' })
      await emitToolStart('browser_navigate', { url: 'https://example.com' })
      expect($rightContextOpen.get()).toBe(false)
      expect($previewTabs.get()).toHaveLength(0)
    })
  })

  describe('generated files (auto-show)', () => {
    it('opens a file the assistant writes during a live turn, and reveals the rail', async () => {
      render(<Harness />)

      await emitTurn('message.start')
      await emitToolComplete(
        'write_file',
        { bytes_written: 42, resolved_path: `${WORKSPACE}/report.html` },
        { path: `${WORKSPACE}/report.html` }
      )

      await waitFor(() => expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/report.html`))
      expect($previewTabs.get()).toHaveLength(1)
      expect($rightContextOpen.get()).toBe(true)
    })

    it('opens a written file whose path is relative to the session workspace', async () => {
      render(<Harness />)

      await emitTurn('message.start')
      await emitToolComplete('write_file', { bytes_written: 9 }, { path: 'notes/summary.md' })

      await waitFor(() => expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/notes/summary.md`))
    })

    it('does not open a file completed outside a live turn, so a replayed completion stays quiet', async () => {
      render(<Harness />)

      await emitToolComplete('write_file', { bytes_written: 42, resolved_path: `${WORKSPACE}/old.html` })
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
    })

    it('opens a file once per turn, and not again after the user closes it in that turn', async () => {
      render(<Harness />)
      const write = { bytes_written: 1, resolved_path: `${WORKSPACE}/page.html` }

      await emitTurn('message.start')
      await emitToolComplete('write_file', write)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(1))

      closeRightRail()
      await emitToolComplete('patch', { ...write, files_modified: [`${WORKSPACE}/page.html`], success: true })
      await settle()
      expect($previewTabs.get()).toHaveLength(0)

      await emitTurn('message.complete')
      await emitTurn('message.start')
      await emitToolComplete('write_file', write)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(1))
    })

    it('does nothing when the Settings switch is off', async () => {
      setAutoShowGeneratedFiles(false)
      expect($autoShowGeneratedFiles.get()).toBe(false)
      render(<Harness />)

      await emitTurn('message.start')
      await emitToolComplete('write_file', { bytes_written: 1, resolved_path: `${WORKSPACE}/page.html` })
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
    })

    it('never opens a file the assistant only read', async () => {
      render(<Harness />)

      await emitTurn('message.start')
      await emitToolComplete('read_file', { content: '<html></html>' }, { path: `${WORKSPACE}/page.html` })
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
    })

    it('does not open a write whose tool call failed', async () => {
      render(<Harness />)

      await emitTurn('message.start')
      await emitToolComplete('write_file', { error: 'Permission denied' }, { path: `${WORKSPACE}/page.html` })
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
    })

    it('keeps a write outside the session workspace closed', async () => {
      render(<Harness />)

      await emitTurn('message.start')
      await emitToolComplete('write_file', { bytes_written: 1, resolved_path: '/tmp/page.html' })
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
    })

    it('never opens for a background session, even mid-turn', async () => {
      $sessionStates.set({ 'background-session': { cwd: WORKSPACE } as never })
      render(<Harness />)

      await emitTurn('message.start', 'background-session')
      await emitToolComplete(
        'write_file',
        { bytes_written: 1, resolved_path: `${WORKSPACE}/bg.html` },
        {},
        'background-session'
      )
      await settle()

      expect($previewTabs.get()).toHaveLength(0)
      expect($rightContextOpen.get()).toBe(false)
    })

    it('does not move focus off the composer while the file opens', async () => {
      const composer = window.document.createElement('textarea')

      window.document.body.append(composer)
      composer.focus()

      try {
        render(<Harness />)

        await emitTurn('message.start')
        await emitToolComplete('write_file', { bytes_written: 1, resolved_path: `${WORKSPACE}/report.pdf` })
        await waitFor(() => expect($previewTabs.get()).toHaveLength(1))

        expect(window.document.activeElement).toBe(composer)
      } finally {
        composer.remove()
      }
    })

    it('adds the tab but leaves the rail closed when the user already hid it this turn', async () => {
      render(<Harness />)

      await emitTurn('message.start')
      await emitToolComplete('write_file', { bytes_written: 1, resolved_path: `${WORKSPACE}/first.html` })
      await waitFor(() => expect($rightContextOpen.get()).toBe(true))

      setRightContextOpen(false)
      await emitToolComplete('write_file', { bytes_written: 1, resolved_path: `${WORKSPACE}/second.html` })

      await waitFor(() => expect($previewTabs.get()).toHaveLength(2))
      expect($rightContextOpen.get()).toBe(false)
    })
  })

  describe('close_preview', () => {
    it('closes the whole pane when no url is given', async () => {
      render(<Harness />)

      await emitPreviewOpen(`${WORKSPACE}/one.html`)
      await emitPreviewOpen(`${WORKSPACE}/two.html`)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(2))

      await emitPreviewClose('')

      expect($previewTabs.get()).toHaveLength(0)
      expect($previewTarget.get()).toBeNull()
    })

    it('closes only the matching tab when a url is given', async () => {
      render(<Harness />)

      await emitPreviewOpen(`${WORKSPACE}/keep.html`)
      await emitPreviewOpen(`${WORKSPACE}/drop.html`)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(2))

      await emitPreviewClose(`${WORKSPACE}/drop.html`)

      await waitFor(() => expect($previewTabs.get()).toHaveLength(1))
      expect($previewTarget.get()?.path).toBe(`${WORKSPACE}/keep.html`)
    })

    it('ignores a close from a session that is not the one on screen', async () => {
      render(<Harness />)

      await emitPreviewOpen(`${WORKSPACE}/stay.html`)
      await waitFor(() => expect($previewTabs.get()).toHaveLength(1))

      await emitPreviewClose(`${WORKSPACE}/stay.html`, 'some-other-session')

      expect($previewTabs.get()).toHaveLength(1)
    })

    it('honors a close from an open tile session even when main holds focus', async () => {
      const tiles = $sessionTiles.get()

      $sessionTiles.set([{ dir: 'right', runtimeId: 'tile-runtime', storedSessionId: 'stored-tile' }])
      $sessionStates.set({ 'tile-runtime': { cwd: WORKSPACE } as never })
      render(<Harness />)

      try {
        await emitPreviewOpen(`${WORKSPACE}/from-tile.html`, 'tile-runtime')
        await waitFor(() => expect($previewTabs.get()).toHaveLength(1))

        await emitPreviewClose(`${WORKSPACE}/from-tile.html`, 'tile-runtime')

        await waitFor(() => expect($previewTabs.get()).toHaveLength(0))
      } finally {
        $sessionTiles.set(tiles)
      }
    })

    it('is a no-op when nothing is open', async () => {
      render(<Harness />)

      await emitPreviewClose()

      expect($previewTabs.get()).toHaveLength(0)
    })
  })
})
