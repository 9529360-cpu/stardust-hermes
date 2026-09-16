import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n'
import { NEW_SESSION_TITLE, sessionTitle } from '@/lib/chat-runtime'
import { cn } from '@/lib/utils'
import { $newChatProfile } from '@/store/profile'
import { $projectTree, refreshProjectTree, scanAndRecordRepos } from '@/store/projects'
import { $rightContextOpen, setRightContextOpen } from '@/store/right-context'
import { $gatewayState, $sessions } from '@/store/session'
import { $focusedStoredSessionId, $workingSessionIds } from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import { CRON_ROUTE, SETTINGS_ROUTE, SKILLS_ROUTE, type AppView } from '../routes'
import type { SidebarNavItem } from '../types'
import type { SidebarActions } from './types'

const COPY = {
  ar: { conversations: 'المحادثات', home: 'الرئيسية', newTask: 'مهمة جديدة', projects: 'المشاريع', search: 'بحث', settings: 'الإعدادات', skills: 'Skills / MCP', tasks: 'المهام', workspace: 'مساحة العمل' },
  en: { conversations: 'Conversations', home: 'Home', newTask: 'New task', projects: 'Projects', search: 'Search tasks', settings: 'Settings', skills: 'Skills / MCP', tasks: 'Tasks', workspace: 'Workspace' },
  ja: { conversations: '会話', home: 'ホーム', newTask: '新しいタスク', projects: 'プロジェクト', search: 'タスクを検索', settings: '設定', skills: 'Skills / MCP', tasks: 'タスク', workspace: 'ワークスペース' },
  ru: { conversations: 'Диалоги', home: 'Главная', newTask: 'Новая задача', projects: 'Проекты', search: 'Поиск задач', settings: 'Настройки', skills: 'Skills / MCP', tasks: 'Задачи', workspace: 'Рабочая область' },
  zh: { conversations: '会话', home: '主页', newTask: '新建任务', projects: '项目', search: '搜索任务', settings: '设置', skills: 'Skills / MCP', tasks: '任务', workspace: '工作区' },
  'zh-hant': { conversations: '對話', home: '首頁', newTask: '新增任務', projects: '專案', search: '搜尋任務', settings: '設定', skills: 'Skills / MCP', tasks: '任務', workspace: '工作區' }
} as const

const NULL_ICON: SidebarNavItem['icon'] = () => null

interface TaskRailProps {
  actions: SidebarActions
  currentView: AppView
}

function RailSectionTitle({ children }: { children: string }) {
  return (
    <div className="px-2 text-[0.58rem] font-semibold uppercase tracking-[0.14em] text-(--ui-text-quaternary)" data-task-rail-section-title="">
      {children}
    </div>
  )
}

function RailNavButton({ active = false, icon, label, onClick }: { active?: boolean; icon: string; label: string; onClick: () => void }) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[0.72rem] font-medium transition-colors',
        active
          ? 'bg-(--ui-control-active-background) text-(--ui-text-primary)'
          : 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)'
      )}
      onClick={onClick}
      type="button"
    >
      <Codicon className="shrink-0" name={icon} size="0.85rem" />
      <span className="truncate">{label}</span>
    </button>
  )
}

function sessionMatchesQuery(session: SessionInfo, query: string): boolean {
  if (!query) {
    return true
  }

  const haystack = [sessionTitle(session), session.model, session.cwd, session.provider]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase()

  return haystack.includes(query.toLocaleLowerCase())
}

export function TaskRail({ actions, currentView }: TaskRailProps) {
  const { locale } = useI18n()
  const sessions = useStore($sessions)
  const focusedSessionId = useStore($focusedStoredSessionId)
  const workingSessionIds = useStore($workingSessionIds)
  const projectTree = useStore($projectTree)
  const gatewayState = useStore($gatewayState)
  const rightContextOpen = useStore($rightContextOpen)
  const [query, setQuery] = useState('')
  const copy = COPY[locale]

  // Projects are first-class Stardust navigation, not an optional grouping.
  // Fetch the fast cached/tree view first, then perform the heavier repo crawl
  // in the background so a new repository appears without blocking first paint.
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    let cancelled = false

    void refreshProjectTree().finally(() => {
      if (!cancelled) {
        void scanAndRecordRepos()
      }
    })

    return () => {
      cancelled = true
    }
  }, [gatewayState])

  const visibleSessions = useMemo(
    () => sessions.filter(session => !session.archived && sessionMatchesQuery(session, query.trim())).slice(0, 14),
    [query, sessions]
  )

  const projects = useMemo(() => projectTree.filter(project => !project.isNoProject).slice(0, 5), [projectTree])

  const navigate = (item: SidebarNavItem) => actions.onNavigate(item)
  const newTask = () => {
    $newChatProfile.set(null)
    navigate({ id: 'new-session', label: copy.newTask, icon: NULL_ICON, action: 'new-session' })
  }

  return (
    <aside className="flex h-full min-h-0 flex-col" data-slot="sidebar" data-stardust-task-rail="">
      <header className="shrink-0" data-task-rail-header="">
        <div className="flex items-center gap-2.5" data-task-rail-brand="">
          <span aria-hidden="true" className="grid size-7 place-items-center rounded-[10px]" data-task-rail-mark="">✦</span>
          <div className="min-w-0">
            <div className="truncate text-[0.72rem] font-semibold tracking-[0.16em] text-(--ui-text-primary)">STARDUST</div>
            <div className="mt-0.5 text-[0.58rem] text-(--ui-text-quaternary)">AI WORKSPACE</div>
          </div>
        </div>

        <button
          className="mt-3 flex h-10 w-full items-center gap-2 rounded-xl px-3 text-left text-[0.76rem] font-semibold"
          data-task-rail-new
          onClick={newTask}
          type="button"
        >
          <Codicon name="add" size="0.9rem" />
          <span>{copy.newTask}</span>
        </button>

        <div className="mt-3" data-task-rail-search="">
          <SearchField aria-label={copy.search} onChange={setQuery} placeholder={copy.search} value={query} />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4" data-task-rail-scroll="">
        {projects.length > 0 && (
          <section className="mb-4">
            <RailSectionTitle>{copy.projects}</RailSectionTitle>
            <div className="mt-1.5 flex flex-col gap-0.5">
              {projects.map(project => {
                const cwd = (project.path || project.repos.find(repo => repo.path)?.path || '').trim()

                return (
                  <button
                    className="group flex min-h-8 w-full items-center gap-2 rounded-lg px-2 text-left hover:bg-(--ui-control-hover-background) disabled:opacity-45"
                    disabled={!cwd}
                    key={project.id}
                    onClick={() => cwd && actions.onNewSessionInWorkspace(cwd)}
                    type="button"
                  >
                    <span aria-hidden="true" className="grid size-6 shrink-0 place-items-center rounded-md" data-task-project-icon="">
                      <Codicon name="repo" size="0.76rem" />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[0.69rem] font-medium text-(--ui-text-secondary)">{project.label}</span>
                    <Codicon className="shrink-0 opacity-0 transition-opacity group-hover:opacity-55" name="chevron-right" size="0.68rem" />
                  </button>
                )
              })}
            </div>
          </section>
        )}

        <section>
          <RailSectionTitle>{copy.conversations}</RailSectionTitle>
          <div className="mt-1.5 flex flex-col gap-0.5">
            {visibleSessions.length > 0 ? (
              visibleSessions.map(session => {
                const active = focusedSessionId === session.id
                const working = workingSessionIds.includes(session.id)

                return (
                  <button
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'group flex min-h-10 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors',
                      active ? 'bg-(--ui-control-active-background)' : 'hover:bg-(--ui-control-hover-background)'
                    )}
                    key={session.id}
                    onClick={() => actions.onResumeSession(session.id, session)}
                    type="button"
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        'size-1.5 shrink-0 rounded-full',
                        working ? 'bg-(--theme-midground)' : active ? 'bg-(--ui-success)' : 'bg-(--ui-stroke-primary)'
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          'block truncate text-[0.7rem] font-medium',
                          active ? 'text-(--ui-text-primary)' : 'text-(--ui-text-secondary)'
                        )}
                      >
                        {sessionTitle(session)}
                      </span>
                      <span className="mt-0.5 block truncate text-[0.57rem] text-(--ui-text-quaternary)">
                        {session.model || session.cwd || NEW_SESSION_TITLE}
                      </span>
                    </span>
                  </button>
                )
              })
            ) : (
              <div className="rounded-lg px-2 py-4 text-center text-[0.65rem] text-(--ui-text-quaternary)">
                {query ? 'No matching tasks' : 'No recent tasks'}
              </div>
            )}
          </div>
        </section>

        <section className="mt-4">
          <RailSectionTitle>Tools</RailSectionTitle>
          <div className="mt-1.5">
            <RailNavButton
              icon="tools"
              label={copy.skills}
              onClick={() => navigate({ id: 'skills', label: copy.skills, icon: NULL_ICON, route: SKILLS_ROUTE })}
            />
          </div>
        </section>
      </div>

      <nav aria-label="Product navigation" className="shrink-0" data-task-rail-footer="">
        <RailNavButton active={currentView === 'chat'} icon="home" label={copy.home} onClick={newTask} />
        <RailNavButton
          active={currentView === 'cron'}
          icon="checklist"
          label={copy.tasks}
          onClick={() => navigate({ id: 'cron', label: copy.tasks, icon: NULL_ICON, route: CRON_ROUTE })}
        />
        <RailNavButton
          icon="layout-sidebar-right"
          label={copy.workspace}
          onClick={() => setRightContextOpen(!rightContextOpen)}
        />
        <RailNavButton
          active={currentView === 'settings'}
          icon="settings-gear"
          label={copy.settings}
          onClick={() => navigate({ id: 'settings', label: copy.settings, icon: NULL_ICON, route: SETTINGS_ROUTE })}
        />
      </nav>
    </aside>
  )
}
