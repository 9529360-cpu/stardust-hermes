import { useStore } from '@nanostores/react'

import { revealTreePane } from '@/components/pane-shell/tree/store'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $newChatProfile } from '@/store/profile'
import { $rightContextOpen, setRightContextOpen } from '@/store/right-context'

import { CRON_ROUTE, SETTINGS_ROUTE, type AppView } from '../routes'
import type { SidebarNavItem } from '../types'
import './personal-product-nav.css'
import { WORKSPACE_OVERVIEW_PANE_ID } from './workspace-overview'

const PRODUCT_NAV_COPY = {
  ar: { home: 'الرئيسية', newTask: 'مهمة جديدة', settings: 'الإعدادات', tasks: 'المهام', workspace: 'مساحة العمل' },
  en: { home: 'Home', newTask: 'New task', settings: 'Settings', tasks: 'Tasks', workspace: 'Workspace' },
  ja: { home: 'ホーム', newTask: '新しいタスク', settings: '設定', tasks: 'タスク', workspace: 'ワークスペース' },
  ru: { home: 'Главная', newTask: 'Новая задача', settings: 'Настройки', tasks: 'Задачи', workspace: 'Рабочая область' },
  zh: { home: '主页', newTask: '新建任务', settings: '设置', tasks: '任务', workspace: '工作区' },
  'zh-hant': { home: '首頁', newTask: '新增任務', settings: '設定', tasks: '任務', workspace: '工作區' }
} as const

const NULL_ICON: SidebarNavItem['icon'] = () => null

interface PersonalProductNavProps {
  currentView: AppView
  onNavigate: (item: SidebarNavItem) => void
}

interface ProductNavButtonProps {
  active?: boolean
  expanded?: boolean
  icon: string
  label: string
  onClick: () => void
}

function ProductNavButton({ active = false, expanded = false, icon, label, onClick }: ProductNavButtonProps) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      aria-expanded={expanded || undefined}
      className={cn(
        'flex h-8 w-full items-center gap-2 rounded-lg border border-transparent px-2.5 text-left text-[0.72rem] font-medium transition-colors',
        active
          ? 'border-(--ui-stroke-tertiary) bg-(--ui-control-active-background) text-(--ui-text-primary)'
          : expanded
            ? 'text-(--ui-text-secondary)'
            : 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)'
      )}
      onClick={onClick}
      type="button"
    >
      <Codicon className="shrink-0" name={icon} size="0.88rem" />
      <span className="truncate">{label}</span>
      {expanded && !active && <span aria-hidden="true" className="ml-auto size-1.5 rounded-full bg-(--theme-midground)" />}
    </button>
  )
}

export function ProductRailHeader({ onNavigate }: { onNavigate: (item: SidebarNavItem) => void }) {
  const { locale } = useI18n()
  const copy = PRODUCT_NAV_COPY[locale]

  const newTask = () => {
    $newChatProfile.set(null)
    onNavigate({
      id: 'new-session',
      label: copy.newTask,
      icon: NULL_ICON,
      action: 'new-session'
    })
  }

  return (
    <div className="absolute inset-x-0 top-0 z-30" data-product-rail-header="">
      <div className="flex items-center gap-2.5" data-product-brand="">
        <span aria-hidden="true" className="grid size-7 place-items-center rounded-lg" data-product-brand-mark="">
          ✦
        </span>
        <span className="text-[0.72rem] font-semibold tracking-[0.16em]">STARDUST</span>
      </div>

      <button className="flex w-full items-center gap-2 rounded-xl px-3 text-left text-[0.76rem] font-semibold" onClick={newTask} type="button" data-product-new-task="">
        <Codicon name="add" size="0.92rem" />
        <span>{copy.newTask}</span>
        <span aria-hidden="true" className="ml-auto text-[0.62rem] opacity-65">⌘N</span>
      </button>
    </div>
  )
}

export function PersonalProductNav({ currentView, onNavigate }: PersonalProductNavProps) {
  const { locale } = useI18n()
  const copy = PRODUCT_NAV_COPY[locale]
  const rightContextOpen = useStore($rightContextOpen)

  const openHome = () =>
    onNavigate({
      id: 'new-session',
      label: copy.home,
      icon: NULL_ICON,
      action: 'new-session'
    })

  const openTasks = () =>
    onNavigate({
      id: 'cron',
      label: copy.tasks,
      icon: NULL_ICON,
      route: CRON_ROUTE
    })

  const openSettings = () =>
    onNavigate({
      id: 'settings',
      label: copy.settings,
      icon: NULL_ICON,
      route: SETTINGS_ROUTE
    })

  const openWorkspace = () => {
    setRightContextOpen(true)
    revealTreePane(WORKSPACE_OVERVIEW_PANE_ID)
  }

  return (
    <nav aria-label="Product navigation" className="absolute inset-x-3 bottom-3 z-20" data-personal-product-nav="">
      <ProductNavButton active={currentView === 'chat'} icon="home" label={copy.home} onClick={openHome} />
      <ProductNavButton active={currentView === 'cron'} icon="checklist" label={copy.tasks} onClick={openTasks} />
      <ProductNavButton expanded={rightContextOpen} icon="layout-sidebar-right" label={copy.workspace} onClick={openWorkspace} />
      <ProductNavButton active={currentView === 'settings'} icon="settings-gear" label={copy.settings} onClick={openSettings} />
    </nav>
  )
}