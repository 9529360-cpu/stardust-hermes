import './personal-product-nav.css'

import { useStore } from '@nanostores/react'
import { useLocation } from 'react-router'

import { revealTreePane } from '@/components/pane-shell/tree/store'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $sidebarGrouping, setSidebarAgentsGrouped, setSidebarOpen } from '@/store/layout'
import { $newChatProfile } from '@/store/profile'
import { exitProjectScope } from '@/store/projects'
import { $selectedStoredSessionId } from '@/store/session'
import { $focusedSessionIsTile } from '@/store/session-states'

import { type AppView, CRON_ROUTE, NEW_CHAT_ROUTE, sessionRoute, SKILLS_ROUTE } from '../routes'
import type { SidebarNavItem } from '../types'

const PRODUCT_NAV_COPY = {
  ar: { newChat: 'محادثة جديدة', plugins: 'الإضافات', project: 'المشروع', tasks: 'المهام', tools: 'الأدوات' },
  en: { newChat: 'New chat', plugins: 'Plugins', project: 'Project', tasks: 'Tasks', tools: 'Tools' },
  ja: { newChat: '新しいチャット', plugins: 'プラグイン', project: 'プロジェクト', tasks: 'タスク', tools: 'ツール' },
  ru: { newChat: 'Новый чат', plugins: 'Плагины', project: 'Проект', tasks: 'Задачи', tools: 'Инструменты' },
  zh: { newChat: '新建对话', plugins: '插件', project: '项目', tasks: '任务', tools: '工具' },
  'zh-hant': { newChat: '新增對話', plugins: '外掛', project: '專案', tasks: '任務', tools: '工具' }
} as const

const NULL_ICON: SidebarNavItem['icon'] = () => null

interface PersonalProductNavProps {
  currentView: AppView
  onNavigate: (item: SidebarNavItem) => void
}

interface ProductNavButtonProps {
  active?: boolean
  icon: string
  label: string
  onClick: () => void
  tour?: string
}

function ProductNavButton({ active = false, icon, label, onClick, tour }: ProductNavButtonProps) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[0.74rem] font-medium transition-colors',
        active
          ? 'bg-(--ui-control-active-background) text-(--ui-text-primary)'
          : 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)'
      )}
      data-tour={tour}
      onClick={onClick}
      type="button"
    >
      <Codicon className="shrink-0" name={icon} size="0.88rem" />
      <span className="truncate">{label}</span>
    </button>
  )
}

export function PersonalProductNav({ currentView: routeView, onNavigate }: PersonalProductNavProps) {
  const { locale } = useI18n()
  const { search } = useLocation()
  const copy = PRODUCT_NAV_COPY[locale]
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const sidebarGrouping = useStore($sidebarGrouping)
  // Selection follows the focused pane, exactly as the conversation list below
  // does: while a session tile owns focus, no page is current even if the
  // workspace keeps that page's route.
  const focusedSessionIsTile = useStore($focusedSessionIsTile)
  const currentView = focusedSessionIsTile ? 'chat' : routeView
  const skillsTab = new URLSearchParams(search).get('tab')

  const newChat = () => {
    setSidebarAgentsGrouped(false)
    setSidebarOpen(true)
    revealTreePane('sessions')
    // A plain new chat lands in the live profile, matching the `session.new`
    // keybind; a prior per-profile quick-create must not leak into it.
    $newChatProfile.set(null)
    onNavigate({
      action: 'new-session',
      id: 'new-session',
      icon: NULL_ICON,
      label: copy.newChat
    })
  }

  const openTasks = () =>
    onNavigate({
      id: 'cron',
      label: copy.tasks,
      icon: NULL_ICON,
      route: CRON_ROUTE
    })

  const openTools = () =>
    onNavigate({
      id: 'skills',
      label: copy.tools,
      icon: NULL_ICON,
      route: `${SKILLS_ROUTE}?tab=toolsets`
    })

  const openPlugins = () =>
    onNavigate({
      id: 'plugins',
      label: copy.plugins,
      icon: NULL_ICON,
      route: `${SKILLS_ROUTE}?tab=plugins`
    })

  const openProject = () => {
    setSidebarAgentsGrouped(true)
    exitProjectScope()
    setSidebarOpen(true)
    revealTreePane('sessions')

    if (routeView !== 'chat') {
      onNavigate({
        id: 'project',
        label: copy.project,
        icon: NULL_ICON,
        route: selectedStoredSessionId ? sessionRoute(selectedStoredSessionId) : NEW_CHAT_ROUTE
      })
    }
  }

  return (
    <nav
      aria-label="Product navigation"
      className="jarvis-product-nav relative isolate shrink-0 overflow-hidden border-b border-(--ui-stroke-tertiary) bg-(--ui-sidebar-surface-background) px-2.5 pb-2 pt-2.5"
      data-personal-product-nav=""
    >
      <div className="mb-3 px-2 text-[0.82rem] font-semibold tracking-[-0.01em] text-(--ui-text-primary)">Stardust</div>
      <div className="flex flex-col gap-0.5">
        <ProductNavButton icon="add" label={copy.newChat} onClick={newChat} tour="sidebar-nav-new-session" />
        <ProductNavButton
          active={currentView === 'cron'}
          icon="checklist"
          label={copy.tasks}
          onClick={openTasks}
          tour="sidebar-nav-cron"
        />
        <ProductNavButton
          active={currentView === 'skills' && skillsTab !== 'plugins'}
          icon="tools"
          label={copy.tools}
          onClick={openTools}
          tour="sidebar-nav-skills"
        />
        <ProductNavButton
          active={currentView === 'skills' && skillsTab === 'plugins'}
          icon="plug"
          label={copy.plugins}
          onClick={openPlugins}
        />
        <ProductNavButton
          active={currentView === 'chat' && sidebarGrouping === 'project'}
          icon="repo"
          label={copy.project}
          onClick={openProject}
        />
      </div>
    </nav>
  )
}
