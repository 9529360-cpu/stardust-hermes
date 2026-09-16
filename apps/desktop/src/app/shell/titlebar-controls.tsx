import { compactNumber } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { type ComponentProps, type MouseEvent, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tip, TipKeybindLabel } from '@/components/ui/tooltip'
import { Slot } from '@/contrib/react/slot'
import { useContributions } from '@/contrib/react/use-contributions'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { cn } from '@/lib/utils'
import {
  $fileBrowserOpen,
  $panesFlipped,
  $sidebarOpen,
  toggleFileBrowserOpen,
  toggleSidebarOpen
} from '@/store/layout'
import { $unreadSessionCount } from '@/store/session-dot-state'
import { $titlebarAppActionsSide } from '@/store/titlebar-app-actions'

import { appViewForPath, hidesFixedTitlebarClusters, isOverlayView } from '../routes'

import { titlebarButtonClass, titlebarToolClusterClass } from './titlebar'
import { TitlebarIcon } from './titlebar-icon'

export interface TitlebarTool {
  id: string
  label: string
  active?: boolean
  className?: string
  disabled?: boolean
  hidden?: boolean
  href?: string
  icon: ReactNode
  onSelect?: (event?: MouseEvent) => void
  /** Keybind action id — when set, the tooltip shows the label + keybind hint. */
  actionId?: string
  /** Overlay count on the glyph (unread sessions). Hidden when 0/undefined. */
  badge?: number
  title?: string
  to?: string
  /** Durable `data-tour` handle. Tools are addressed by icon and translated
   *  label otherwise, and neither survives a theme or a locale change. */
  tour?: string
}

export type TitlebarToolSide = 'left' | 'right'
export type SetTitlebarToolGroup = (id: string, tools: readonly TitlebarTool[], side?: TitlebarToolSide) => void

interface TitlebarControlsProps extends ComponentProps<'div'> {
  leftTools?: readonly TitlebarTool[]
  tools?: readonly TitlebarTool[]
  onOpenSettings: () => void
}

/** Overlay count on a titlebar glyph. Hidden when count is 0/undefined. */
function withCountBadge(icon: ReactNode, count: number | undefined): ReactNode {
  if (!count) {
    return icon
  }

  return (
    <span className="relative inline-flex">
      {icon}
      <span className="pointer-events-none absolute -top-2.5 -right-1.5 z-1">
        <Badge aria-hidden size="overlay" variant="solid">
          {compactNumber(count)}
        </Badge>
      </span>
    </span>
  )
}

export function TitlebarControls({ leftTools = [], tools = [], onOpenSettings }: TitlebarControlsProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const fileBrowserOpen = useStore($fileBrowserOpen)
  const panesFlipped = useStore($panesFlipped)
  const sidebarOpen = useStore($sidebarOpen)
  const unreadCount = useStore($unreadSessionCount)
  const appActionsSide = useStore($titlebarAppActionsSide)
  const unreadBadge = unreadCount > 0 ? unreadCount : undefined
  const unreadHint = unreadBadge ? ` · ${t.titlebar.unreadSessions(unreadBadge)}` : ''

  const titleBarLeft = useContributions('titleBar.left')
  const titleBarCenter = useContributions('titleBar.center')
  const titleBarRight = useContributions('titleBar.right')
  const pageOwnsTitlebar = titleBarLeft.length + titleBarCenter.length + titleBarRight.length > 0

  // Keep the everyday chrome tiny. Layout editing, pane flipping and HUD still
  // exist through their keybinds / command-palette actions, but they no longer
  // compete with the conversation for permanent titlebar space.
  const leftEdge = { open: sidebarOpen, toggle: toggleSidebarOpen }
  const rightEdge = { open: fileBrowserOpen, toggle: toggleFileBrowserOpen }
  const leftLabel = leftEdge.open ? t.titlebar.hideSidebar : t.titlebar.showSidebar
  const rightLabel = rightEdge.open ? t.titlebar.hideRightSidebar : t.titlebar.showRightSidebar

  const sidebarTool: TitlebarTool = {
    actionId: 'view.toggleSidebar',
    badge: panesFlipped ? undefined : unreadBadge,
    icon: <TitlebarIcon name="layout-sidebar-left" />,
    id: 'sidebar',
    label: `${leftLabel}${panesFlipped ? '' : unreadHint}`,
    onSelect: () => {
      triggerHaptic('tap')
      leftEdge.toggle()
    }
  }

  const rightSidebarTool: TitlebarTool = {
    actionId: 'view.toggleRightSidebar',
    badge: panesFlipped ? unreadBadge : undefined,
    icon: <TitlebarIcon name="layout-sidebar-right" />,
    id: 'right-sidebar',
    label: `${rightLabel}${panesFlipped ? unreadHint : ''}`,
    onSelect: () => {
      triggerHaptic('tap')
      rightEdge.toggle()
    },
    tour: 'right-pane-toggle'
  }

  const systemTools: TitlebarTool[] = [
    {
      actionId: 'nav.settings',
      icon: <TitlebarIcon name="settings-gear" />,
      id: 'settings',
      label: t.titlebar.openSettings,
      onSelect: () => {
        triggerHaptic('open')
        onOpenSettings()
      }
    }
  ]

  const view = appViewForPath(location.pathname)

  if (isOverlayView(view)) {
    return null
  }

  const titlebarSlots = (
    <>
      <Slot area="titleBar.left" />
      <Slot area="titleBar.center" />
      <Slot area="titleBar.right" />
    </>
  )

  const leftClusterClass = cn(
    titlebarToolClusterClass,
    'left-(--titlebar-controls-left) top-(--titlebar-controls-top) translate-y-(--titlebar-controls-y-nudge)'
  )

  if (hidesFixedTitlebarClusters(view) && pageOwnsTitlebar) {
    const pageTools = [...leftTools, ...tools].filter(tool => !tool.hidden)

    return (
      <div className={leftClusterClass}>
        {pageTools.map(tool => (
          <TitlebarToolButton key={tool.id} navigate={navigate} tool={tool} />
        ))}
        {titlebarSlots}
      </div>
    )
  }

  const visibleLeftTools = (
    appActionsSide === 'left' ? [sidebarTool, ...systemTools, ...leftTools] : [sidebarTool, ...leftTools]
  ).filter(tool => !tool.hidden)

  const visibleSystemTools = appActionsSide === 'right' ? systemTools.filter(tool => !tool.hidden) : []
  const visiblePaneTools = tools.filter(tool => !tool.hidden)

  return (
    <>
      <div aria-label={t.shell.windowControls} className={leftClusterClass} data-titlebar-cluster="left">
        {visibleLeftTools.map(tool => (
          <TitlebarToolButton key={tool.id} navigate={navigate} tool={tool} />
        ))}
        <Slot area="titleBar.left" />
        <Slot area="titleBar.center" />
      </div>

      {visiblePaneTools.length > 0 && (
        <div
          aria-label={t.shell.appControls}
          className={cn(
            titlebarToolClusterClass,
            'top-[calc(var(--titlebar-controls-top)+var(--right-rail-top-inset,0px))] right-[calc(var(--titlebar-tools-right)+var(--shell-preview-toolbar-gap,0))]'
          )}
        >
          {visiblePaneTools.map(tool => (
            <TitlebarToolButton key={tool.id} navigate={navigate} tool={tool} />
          ))}
        </div>
      )}

      <div
        aria-label={t.shell.appControls}
        className={cn(titlebarToolClusterClass, 'right-(--titlebar-tools-right) top-(--titlebar-controls-top)')}
        data-titlebar-cluster="right"
      >
        {visibleSystemTools.map(tool => (
          <TitlebarToolButton key={tool.id} navigate={navigate} tool={tool} />
        ))}
        <TitlebarToolButton navigate={navigate} tool={rightSidebarTool} />
        <Slot area="titleBar.right" />
      </div>
    </>
  )
}

function TitlebarToolButton({ navigate, tool }: { navigate: ReturnType<typeof useNavigate>; tool: TitlebarTool }) {
  const className = cn(titlebarButtonClass, 'bg-transparent select-none', tool.className)

  const tooltipLabel = tool.actionId ? (
    <TipKeybindLabel actionId={tool.actionId} text={tool.title ?? tool.label} />
  ) : (
    (tool.title ?? tool.label)
  )

  if (tool.href) {
    return (
      <Tip label={tooltipLabel}>
        <Button asChild className={className} size="icon-titlebar" variant="ghost">
          <a
            aria-label={tool.label}
            data-tour={tool.tour}
            href={tool.href}
            onPointerDown={event => event.stopPropagation()}
            rel="noreferrer"
            target="_blank"
          >
            {withCountBadge(tool.icon, tool.badge)}
          </a>
        </Button>
      </Tip>
    )
  }

  return (
    <Tip label={tooltipLabel}>
      <Button
        aria-label={tool.label}
        aria-pressed={tool.active ?? undefined}
        className={className}
        data-tour={tool.tour}
        disabled={tool.disabled}
        onClick={event => {
          if (tool.to) {
            navigate(tool.to)
          }

          tool.onSelect?.(event)
        }}
        onPointerDown={event => event.stopPropagation()}
        size="icon-titlebar"
        type="button"
        variant="ghost"
      >
        {withCountBadge(tool.icon, tool.badge)}
      </Button>
    </Tip>
  )
}
