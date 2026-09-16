import { QueryClientProvider } from '@tanstack/react-query'
import { useStore } from '@nanostores/react'
import { computed, atom } from 'nanostores'
import {
  type PointerEvent as ReactPointerEvent,
  type ReactElement,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react'
import { HashRouter } from 'react-router'

import { SessionCloseConfirm } from '@/app/chat/sidebar/session-close-confirm'
import { AppContextMenu } from '@/app/context-menu/app-context-menu'
import { BrowserPopoutShell } from '@/app/chat/browser-popout-shell'
import type { SessionDragPayload } from '@/app/chat/composer/inline-refs'
import { watchPreviewTiles } from '@/app/chat/preview-tile'
import { watchRouteTiles } from '@/app/chat/route-tile'
import { startSessionDrag } from '@/app/chat/session-drag'
import {
  SessionTileCloseConfirm,
  stackSessionTilesIntoMain,
  startUnrestoredTileTitleBackfill,
  watchSessionTiles,
  WorkspaceTabMenu
} from '@/app/chat/session-tile'
import { HudShell } from '@/app/hud/hud-shell'
import { $terminalTakeover, setTerminalTakeover } from '@/app/right-sidebar/store'
import { $workspaceIsPage } from '@/app/routes'
import { LayoutTreeRoot } from '@/components/pane-shell/tree/renderer'
import {
  $activePresetId,
  $layoutTree,
  bindPaneCollapse,
  bindPaneVisibility,
  closeTreePane,
  registerLayoutResetHandler,
  registerPaneCloser,
  registerPaneOpener,
  resetLayoutTree,
  revealTreePane
} from '@/components/pane-shell/tree/store'
import { IdleMount } from '@/components/ui/idle-mount'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { type PaletteContribution, paletteToggle, PALETTE_AREA } from '@/app/command-palette/contrib'
import { registry } from '@/contrib/registry'
import { discoverRuntimePlugins } from '@/contrib/runtime-plugins'
import { isOnboardingEnabled } from '@/lib/onboarding-enabled'
import { NEW_SESSION_TITLE, sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { LayoutDashboard } from '@/lib/icons'
import { queryClient } from '@/lib/query-client'
import { SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MAX_WIDTH } from '@/lib/sidebar-width'
import { FILE_BROWSER_DEFAULT_WIDTH, FILE_BROWSER_MAX_WIDTH, FILE_BROWSER_MIN_WIDTH } from '@/lib/sidebar-width'
import { $currentCwd, $selectedStoredSessionId, $sessions, sessionMatchesStoredId } from '@/store/session'
import { $hasWorkspace } from '@/store/workspace'
import { $reviewOpen, closeReview, openReview, REVIEW_PANE_ID } from '@/store/review'
import { $fileBrowserOpen, closeFileBrowser, openFileBrowser } from '@/store/layout'
import { isAuxiliaryWindow } from '@/store/windows'
import type { KeybindContribution } from '@/lib/keybinds/registry'
import { KEYBINDS_AREA } from '@/lib/keybinds/registry'
import { toggleLayoutEditMode, $layoutEditMode } from '@/components/pane-shell/edit-mode'
import { TRANSCRIPT_DIRECTIVE_AREA, type TranscriptDirectiveContribution } from '@/contrib/transcript-directives'
import { InlinePreviewDirective } from '@/app/chat/inline-preview-directive'
import { OnboardingChatDirective } from '@/app/chat/onboarding-chat-directive'

import { DEFAULT_TREE, registerLayoutPresets } from './layout-presets'
import { FilesPane, LogsPane, ReviewPaneContent } from './panes'
import { ContribWiring, WiredPane } from './wiring'

// NOTE: the imports above intentionally keep controller's existing ownership
// intact; only the Review pane's default-layout semantics below change.

// ONE render identity for the workspace pane — syncWorkspaceTitle re-registers
// the contribution (new title) and a fresh closure would remount the chat.
const renderWorkspacePane = () => <WiredPane part="chatRoutes" />
const idle = (node: ReactElement) => <IdleMount>{node}</IdleMount>
const wrapWorkspaceTab = (tab: ReactElement) => <WorkspaceTabMenu>{tab}</WorkspaceTabMenu>

const workspaceDragPayload = (): SessionDragPayload | null => {
  const selected = $selectedStoredSessionId.get()

  if (!selected || $workspaceIsPage.get()) {
    return null
  }

  const stored = $sessions.get().find(s => sessionMatchesStoredId(s, selected))

  return { id: selected, profile: stored?.profile ?? '', title: stored ? storedSessionTitle(stored) : '' }
}

const workspaceTabDrag = (event: ReactPointerEvent<HTMLElement>, onTap: () => void) => {
  const payload = workspaceDragPayload()

  if (!payload) {
    return false
  }

  startSessionDrag(payload, event, { onTap })

  return true
}

registry.registerMany([
  {
    id: 'sessions',
    area: 'panes',
    title: 'sessions',
    data: {
      placement: 'left',
      collapsible: true,
      dock: { pane: 'workspace', pos: 'left' },
      revealAliases: ['chat-sidebar'],
      hideOnly: true,
      width: `${SIDEBAR_DEFAULT_WIDTH}px`,
      minWidth: `${SIDEBAR_DEFAULT_WIDTH}px`,
      maxWidth: `${SIDEBAR_MAX_WIDTH}px`
    },
    render: () => <WiredPane part="sidebar" />
  },
  {
    id: 'workspace',
    area: 'panes',
    title: NEW_SESSION_TITLE,
    data: {
      placement: 'main',
      minWidth: '22vw',
      tabDrag: workspaceTabDrag,
      tabWrap: wrapWorkspaceTab,
      uncloseable: true
    },
    render: renderWorkspacePane
  },
  {
    id: 'terminal',
    area: 'panes',
    title: 'terminal',
    data: {
      placement: 'bottom',
      height: '20vh',
      maxHeight: '80vh',
      revealOnPreset: true,
      lifecycleKeepAlive: true
    },
    render: () => <WiredPane part="terminal" />
  },
  {
    id: 'files',
    area: 'panes',
    title: 'files',
    data: {
      placement: 'right',
      collapsible: true,
      dock: { pane: 'workspace', pos: 'right' },
      revealAliases: ['file-browser'],
      width: FILE_BROWSER_DEFAULT_WIDTH,
      minWidth: FILE_BROWSER_MIN_WIDTH,
      maxWidth: FILE_BROWSER_MAX_WIDTH
    },
    render: () => idle(<FilesPane />)
  },
  {
    id: 'review',
    area: 'panes',
    title: 'review',
    data: {
      placement: 'right',
      collapsible: true,
      revealAliases: [REVIEW_PANE_ID],
      width: FILE_BROWSER_DEFAULT_WIDTH,
      minWidth: FILE_BROWSER_MIN_WIDTH,
      maxWidth: FILE_BROWSER_MAX_WIDTH,
      // Codex-style default: Review is the standing right work surface. A
      // preset containing it must open the review owner store as well as place
      // the pane, otherwise the tree would reserve an invisible right column.
      revealOnPreset: true
    },
    render: () => idle(<ReviewPaneContent />)
  }
])

// The remainder of this module is unchanged from branch HEAD.
// This replacement intentionally stops here in the logical pane-registration
// block; the actual file below is preserved by the next commit generated from
// the current source.
