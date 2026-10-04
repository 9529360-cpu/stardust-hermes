import './personal-product-nav.css'

import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router'

import { revealTreePane } from '@/components/pane-shell/tree/store'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $sidebarGrouping, setSidebarAgentsGrouped, setSidebarOpen } from '@/store/layout'
import { $newChatProfile } from '@/store/profile'
import {
  $projectScope,
  $projectTree,
  enterProject,
  fetchProjectSessions,
  openProjectCreate,
  projectRootCwd,
  refreshProjects,
  refreshProjectTree
} from '@/store/projects'
import { $currentCwd, setCurrentCwd } from '@/store/session'
import { $focusedSessionIsTile } from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import type { SidebarProjectTree } from '../chat/sidebar/projects/workspace-groups'
import { type AppView, CRON_ROUTE, SKILLS_ROUTE } from '../routes'
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
  onResumeSession?: (sessionId: string, session?: SessionInfo) => void
}

interface ProductNavButtonProps {
  active?: boolean
  expanded?: boolean
  icon: string
  label: string
  onClick: () => void
  tour?: string
}

function ProductNavButton({ active = false, expanded, icon, label, onClick, tour }: ProductNavButtonProps) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      aria-expanded={expanded}
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
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {expanded !== undefined && (
        <Codicon className="shrink-0 opacity-60" name={expanded ? 'chevron-down' : 'chevron-right'} size="0.75rem" />
      )}
    </button>
  )
}

export function PersonalProductNav({ currentView: routeView, onNavigate, onResumeSession }: PersonalProductNavProps) {
  const { locale, t } = useI18n()
  const { search } = useLocation()
  const copy = PRODUCT_NAV_COPY[locale]
  const [projectsOpen, setProjectsOpen] = useState(false)
  const [enteredProject, setEnteredProject] = useState<null | SidebarProjectTree>(null)
  const [projectLoadFailed, setProjectLoadFailed] = useState(false)
  const projects = useStore($projectTree)
  const projectScope = useStore($projectScope)
  useEffect(() => {
    if (!projectsOpen || !projects.some(project => project.id === projectScope)) {
      return
    }

    let cancelled = false
    setEnteredProject(null)
    setProjectLoadFailed(false)
    void fetchProjectSessions(projectScope)
      .then(project => {
        if (!cancelled) {setEnteredProject(project)}
      })
      .catch(() => {
        if (!cancelled) {setProjectLoadFailed(true)}
      })

    return () => {
      cancelled = true
    }
  }, [projectsOpen, projectScope, projects])
  const currentCwd = useStore($currentCwd)
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
    // This is a navigation submenu, not an alternate rendering mode for the
    // recents list. Migrate the old project grouping when this control is used.
    if (sidebarGrouping === 'project') {
      setSidebarAgentsGrouped(false)
    }

    setSidebarOpen(true)
    revealTreePane('sessions')

    if (!projectsOpen) {
      void refreshProjects()
      void refreshProjectTree()
    }

    setProjectsOpen(!projectsOpen)
  }

  const selectProject = (id: string) => {
    setSidebarAgentsGrouped(false)
    enterProject(id)
    const cwd = projectRootCwd(projects.find(project => project.id === id))

    if (cwd && cwd !== currentCwd) {
      setCurrentCwd(cwd)
    }
  }

  return (
    <nav
      aria-label="Product navigation"
      className="jarvis-product-nav relative isolate shrink-0 overflow-hidden border-b border-(--ui-stroke-tertiary) bg-(--ui-sidebar-surface-background) px-2.5 pb-2 pt-0"
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
        <ProductNavButton expanded={projectsOpen} icon="repo" label={copy.project} onClick={openProject} />
        {projectsOpen && (
          <div
            aria-label={t.sidebar.projects.sectionLabel}
            className="ml-5 flex max-h-56 flex-col gap-0.5 overflow-y-auto border-l border-(--ui-stroke-tertiary) pl-2"
          >
            {projects.map(project => (
              <div key={project.id}>
                <button
                  aria-expanded={projectScope === project.id}
                  aria-pressed={projectScope === project.id}
                  className="flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[0.72rem] text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)"
                  onClick={() => selectProject(project.id)}
                  title={project.label}
                  type="button"
                >
                  <Codicon className="shrink-0" name="repo" size="0.75rem" />
                  <span className="min-w-0 flex-1 truncate">{project.label}</span>
                  <Codicon
                    className="shrink-0 opacity-60"
                    name={projectScope === project.id ? 'chevron-down' : 'chevron-right'}
                    size="0.7rem"
                  />
                </button>
                {projectScope === project.id && (
                  <div className="ml-3 border-l border-(--ui-stroke-tertiary) pl-1.5">
                    {projectLoadFailed && (
                      <div className="px-2 py-1 text-xs text-(--ui-text-tertiary)">{t.sidebar.projectLoadFailed}</div>
                    )}
                    {enteredProject?.id === project.id &&
                      (enteredProject.repos.flatMap(repo => repo.groups.flatMap(group => group.sessions)).length ? (
                        enteredProject.repos
                          .flatMap(repo => repo.groups.flatMap(group => group.sessions))
                          .map(session => (
                            <button
                              className="block min-h-8 w-full truncate rounded-md px-2 text-left text-[0.7rem] text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)"
                              key={session.id}
                              onClick={() => onResumeSession?.(session.id, session)}
                              title={session.title ?? session.preview ?? session.id}
                              type="button"
                            >
                              {session.title ?? session.preview ?? session.id}
                            </button>
                          ))
                      ) : (
                        <div className="px-2 py-1 text-xs text-(--ui-text-tertiary)">{t.sidebar.projectEmpty}</div>
                      ))}
                  </div>
                )}
              </div>
            ))}
            <button
              className="flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[0.72rem] text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)"
              onClick={openProjectCreate}
              type="button"
            >
              <Codicon className="shrink-0" name="add" size="0.75rem" />
              {t.sidebar.projects.newButton}
            </button>
          </div>
        )}
      </div>
    </nav>
  )
}
