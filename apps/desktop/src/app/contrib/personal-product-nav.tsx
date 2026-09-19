import './personal-product-nav.css'

import { useStore } from '@nanostores/react'

import { revealTreePane } from '@/components/pane-shell/tree/store'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $sidebarGrouping, setSidebarAgentsGrouped, setSidebarOpen } from '@/store/layout'
import { exitProjectScope } from '@/store/projects'
import { $selectedStoredSessionId } from '@/store/session'

import {
  type AppView,
  CRON_ROUTE,
  NEW_CHAT_ROUTE,
  sessionRoute,
  SETTINGS_ROUTE,
  SKILLS_ROUTE,
  STARMAP_ROUTE
} from '../routes'
import type { SidebarNavItem } from '../types'

const PRODUCT_NAV_COPY = {
  ar: { conversation: 'المحادثة', knowledge: 'المعرفة', project: 'المشروع', settings: 'الإعدادات', tasks: 'المهام', tools: 'الأدوات' },
  en: { conversation: 'Conversation', knowledge: 'Knowledge', project: 'Project', settings: 'Settings', tasks: 'Tasks', tools: 'Tools' },
  ja: { conversation: '対話', knowledge: 'ナレッジ', project: 'プロジェクト', settings: '設定', tasks: 'タスク', tools: 'ツール' },
  ru: { conversation: 'Диалог', knowledge: 'Знания', project: 'Проект', settings: 'Настройки', tasks: 'Задачи', tools: 'Инструменты' },
  zh: { conversation: '对话', knowledge: '知识库', project: '项目', settings: '设置', tasks: '任务', tools: '工具' },
  'zh-hant': { conversation: '對話', knowledge: '知識庫', project: '專案', settings: '設定', tasks: '任務', tools: '工具' }
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
}

function ProductNavButton({ active = false, icon, label, onClick }: ProductNavButtonProps) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[0.74rem] font-medium transition-colors',
        active
          ? 'bg-(--ui-control-active-background) text-(--ui-text-primary)'
          : 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)'
      )}
      onClick={onClick}
      type="button"
    >
      <Codicon className="shrink-0" name={icon} size="0.88rem" />
      <span className="truncate">{label}</span>
    </button>
  )
}

export function PersonalProductNav({ currentView, onNavigate }: PersonalProductNavProps) {
  const { locale } = useI18n()
  const copy = PRODUCT_NAV_COPY[locale]
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const sidebarGrouping = useStore($sidebarGrouping)

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
      route: SKILLS_ROUTE
    })

  const openKnowledge = () =>
    onNavigate({
      id: 'starmap',
      label: copy.knowledge,
      icon: NULL_ICON,
      route: STARMAP_ROUTE
    })

  const openSettings = () =>
    onNavigate({
      id: 'settings',
      label: copy.settings,
      icon: NULL_ICON,
      route: SETTINGS_ROUTE
    })

  const openConversation = () => {
    setSidebarAgentsGrouped(false)
    setSidebarOpen(true)
    revealTreePane('sessions')
    if (currentView !== 'chat') {
      onNavigate({
        id: 'conversation',
        label: copy.conversation,
        icon: NULL_ICON,
        route: selectedStoredSessionId ? sessionRoute(selectedStoredSessionId) : NEW_CHAT_ROUTE
      })
    }
  }

  const openProject = () => {
    setSidebarAgentsGrouped(true)
    exitProjectScope()
    setSidebarOpen(true)
    revealTreePane('sessions')
    if (currentView !== 'chat') {
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
      className={cn(
        'jarvis-product-nav relative isolate flex flex-col overflow-hidden bg-(--ui-sidebar-surface-background) px-2.5 pb-2 pt-[calc(var(--titlebar-height)+0.45rem)]',
        currentView === 'chat' ? 'shrink-0 border-b border-(--ui-stroke-tertiary)' : 'min-h-0 flex-1'
      )}
      data-personal-product-nav=""
    >
      <div className="mb-3 px-2 text-[0.82rem] font-semibold tracking-[-0.01em] text-(--ui-text-primary)">
        Stardust
      </div>
      <div className="flex flex-col gap-0.5">
        <ProductNavButton
          active={currentView === 'chat' && sidebarGrouping !== 'project'}
          icon="comment-discussion"
          label={copy.conversation}
          onClick={openConversation}
        />
        <ProductNavButton active={currentView === 'cron'} icon="checklist" label={copy.tasks} onClick={openTasks} />
        <ProductNavButton
          active={currentView === 'chat' && sidebarGrouping === 'project'}
          icon="repo"
          label={copy.project}
          onClick={openProject}
        />
        <ProductNavButton active={currentView === 'starmap'} icon="library" label={copy.knowledge} onClick={openKnowledge} />
        <ProductNavButton active={currentView === 'skills'} icon="tools" label={copy.tools} onClick={openTools} />
        <ProductNavButton active={currentView === 'settings'} icon="settings-gear" label={copy.settings} onClick={openSettings} />
      </div>
    </nav>
  )
}
