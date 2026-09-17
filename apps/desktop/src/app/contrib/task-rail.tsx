import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n'
import { sessionTitle } from '@/lib/chat-runtime'
import { cn } from '@/lib/utils'
import { $newChatProfile } from '@/store/profile'
import { $projectTree, refreshProjectTree, scanAndRecordRepos } from '@/store/projects'
import { $gatewayState, $sessions } from '@/store/session'
import { $focusedStoredSessionId, $workingSessionIds } from '@/store/session-states'
import type { SessionInfo } from '@/types/hermes'

import { CRON_ROUTE, SETTINGS_ROUTE, SKILLS_ROUTE, type AppView } from '../routes'
import type { SidebarNavItem } from '../types'
import type { SidebarActions } from './types'

const COPY = {
  ar: { automations: 'الأتمتة', newThread: 'محادثة جديدة', noMatches: 'لا توجد نتائج', projects: 'المشاريع', search: 'بحث', settings: 'الإعدادات', skills: 'المهارات', threads: 'المحادثات' },
  en: { automations: 'Automations', newThread: 'New thread', noMatches: 'No matching threads', projects: 'Projects', search: 'Search', settings: 'Settings', skills: 'Skills', threads: 'Threads' },
  ja: { automations: 'オートメーション', newThread: '新しいスレッド', noMatches: '一致するスレッドはありません', projects: 'プロジェクト', search: '検索', settings: '設定', skills: 'Skills', threads: 'スレッド' },
  ru: { automations: 'Автоматизации', newThread: 'Новый тред', noMatches: 'Ничего не найдено', projects: 'Проекты', search: 'Поиск', settings: 'Настройки', skills: 'Skills', threads: 'Треды' },
  zh: { automations: '自动化', newThread: '新建线程', noMatches: '没有匹配的线程', projects: '项目', search: '搜索', settings: '设置', skills: '技能', threads: '线程' },
  'zh-hant': { automations: '自動化', newThread: '新增執行緒', noMatches: '沒有符合的執行緒', projects: '專案', search: '搜尋', settings: '設定', skills: '技能', threads: '執行緒' }
} as const

const NULL_ICON: SidebarNavItem['icon'] = () => null

interface TaskRailProps {
  actions: SidebarActions
  currentView: AppView
}

function SectionLabel({ children }: { children: string }) {
  return <div className="px-2 text-[0.61rem] font-medium text-(--ui-text-quaternary)" data-task-rail-section-title="">{children}</div>
}

function NavRow({ active = false, icon, label, onClick }: { active?: boolean; icon: string; label: string; onClick: () => void }) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[0.7rem] font-medium transition-colors',
        active
          ? 'bg-(--ui-control-active-background) text-(--ui-text-primary)'
          : 'text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-primary)'
      )}
      onClick={onClick}
      type="button"
    >
      <Codicon className="shrink-0 text-(--ui-text-tertiary)" name={icon} size="0.8rem" />
      <span className="truncate">{label}</span>
    </button>
  )
}

function ThreadRow({
  active,
  onClick,
  session,
  working
}: {
  active: boolean
  onClick: () => void
  session: SessionInfo
  working: boolean
}) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group flex min-h-7 w-full min-w-0 items-center gap-2 rounded-md px-2 text-left transition-colors',
        active ? 'bg-(--ui-control-active-background)' : 'hover:bg-(--ui-control-hover-background)'
      )}
      onClick={onClick}
      type="button"
    >
      {working ? (
        <Codicon className="shrink-0 text-(--theme-midground)" name="loading" size="0.68rem" spinning />
      ) : (
        <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', active ? 'bg-(--ui-text-secondary)' : 'bg-(--ui-stroke-primary)')} />
      )}
      <span className={cn('min-w-0 flex-1 truncate text-[0.67rem]', active ? 'font-medium text-(--ui-text-primary)' : 'text-(--ui-text-secondary)')}>
        {sessionTitle(session)}
      </span>
    </button>
  )
}

function matches(session: SessionInfo, query: string): boolean {
  if (!query) {
    return true
  }

  return [sessionTitle(session), session.model, session.cwd, session.source, session.profile]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase()
    .includes(query.toLocaleLowerCase())
}

export function TaskRail({ actions, currentView }: TaskRailProps) {
  const { locale } = useI18n()
  const sessions = useStore($sessions)
  const focusedSessionId = useStore($focusedStoredSessionId)
  const workingSessionIds = useStore($workingSessionIds)
  const projectTree = useStore($projectTree)
  const gatewayState = useStore($gatewayState)
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const copy = COPY[locale]

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

  const projects = useMemo(() => projectTree.filter(project => !project.isNoProject).slice(0, 7), [projectTree])
  const visibleSessions = useMemo(
    () => sessions.filter(session => !session.archived && matches(session, query.trim())).slice(0, 24),
    [query, sessions]
  )
  const previewIds = useMemo(
    () => new Set(projects.flatMap(project => project.previewSessions ?? []).map(session => session.id)),
    [projects]
  )
  const looseThreads = useMemo(
    () => visibleSessions.filter(session => !previewIds.has(session.id)).slice(0, 10),
    [previewIds, visibleSessions]
  )

  const navigate = (item: SidebarNavItem) => actions.onNavigate(item)
  const newThread = () => {
    $newChatProfile.set(null)
    navigate({ id: 'new-session', label: copy.newThread, icon: NULL_ICON, action: 'new-session' })
  }
  const openSession = (session: SessionInfo) => actions.onResumeSession(session.id, session)

  return (
    <aside className="flex h-full min-h-0 flex-col" data-slot="sidebar" data-stardust-task-rail="">
      <div aria-hidden="true" className="h-7 shrink-0" data-task-rail-drag="" />

      <div className="shrink-0 px-2 pb-2" data-task-rail-actions="">
        <div className="px-2 pb-1.5 text-[0.64rem] font-semibold tracking-[0.08em] text-(--ui-text-tertiary)" data-task-rail-wordmark="">
          STARDUST
        </div>
        <NavRow icon="add" label={copy.newThread} onClick={newThread} />
        <NavRow
          active={searchOpen}
          icon="search"
          label={copy.search}
          onClick={() => {
            setSearchOpen(open => !open)
            if (searchOpen) {
              setQuery('')
            }
          }}
        />
        <NavRow
          active={currentView === 'cron'}
          icon="history"
          label={copy.automations}
          onClick={() => navigate({ id: 'cron', label: copy.automations, icon: NULL_ICON, route: CRON_ROUTE })}
        />
        <NavRow
          active={currentView === 'skills'}
          icon="sparkle"
          label={copy.skills}
          onClick={() => navigate({ id: 'skills', label: copy.skills, icon: NULL_ICON, route: SKILLS_ROUTE })}
        />

        {searchOpen && (
          <div className="mt-2" data-task-rail-search="">
            <SearchField autoFocus aria-label={copy.search} onChange={setQuery} placeholder={copy.search} value={query} />
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" data-task-rail-scroll="">
        {query.trim() ? (
          <section className="pt-2">
            <SectionLabel>{copy.threads}</SectionLabel>
            <div className="mt-1.5 flex flex-col gap-px">
              {visibleSessions.length > 0 ? (
                visibleSessions.map(session => (
                  <ThreadRow
                    active={focusedSessionId === session.id}
                    key={session.id}
                    onClick={() => openSession(session)}
                    session={session}
                    working={workingSessionIds.includes(session.id)}
                  />
                ))
              ) : (
                <div className="px-2 py-4 text-[0.65rem] text-(--ui-text-quaternary)">{copy.noMatches}</div>
              )}
            </div>
          </section>
        ) : (
          <>
            <section className="pt-2">
              <SectionLabel>{copy.projects}</SectionLabel>
              <div className="mt-1.5 flex flex-col gap-1.5">
                {projects.map(project => {
                  const cwd = (project.path || project.repos.find(repo => repo.path)?.path || '').trim()
                  const projectThreads = (project.previewSessions ?? []).filter(session => !session.archived).slice(0, 5)

                  return (
                    <div key={project.id} data-task-project="">
                      <div className="group flex min-w-0 items-center gap-1 rounded-md px-1 hover:bg-(--ui-control-hover-background)" data-task-project-row="">
                        <Codicon className="ml-1 shrink-0 text-(--ui-text-tertiary)" name="folder" size="0.74rem" />
                        <span className="min-w-0 flex-1 truncate py-1.5 text-[0.68rem] font-medium text-(--ui-text-secondary)">{project.label}</span>
                        <button
                          aria-label={`${copy.newThread} — ${project.label}`}
                          className="grid size-6 shrink-0 place-items-center rounded-md text-(--ui-text-quaternary) opacity-0 hover:bg-(--ui-control-active-background) hover:text-(--ui-text-primary) group-hover:opacity-100 disabled:hidden"
                          disabled={!cwd}
                          onClick={() => cwd && actions.onNewSessionInWorkspace(cwd)}
                          type="button"
                        >
                          <Codicon name="add" size="0.72rem" />
                        </button>
                      </div>

                      {projectThreads.length > 0 && (
                        <div className="ml-3 border-l border-(--ui-stroke-quaternary) pl-1.5" data-task-project-threads="">
                          {projectThreads.map(session => (
                            <ThreadRow
                              active={focusedSessionId === session.id}
                              key={session.id}
                              onClick={() => openSession(session)}
                              session={session}
                              working={workingSessionIds.includes(session.id)}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </section>

            {looseThreads.length > 0 && (
              <section className="mt-4">
                <SectionLabel>{copy.threads}</SectionLabel>
                <div className="mt-1.5 flex flex-col gap-px">
                  {looseThreads.map(session => (
                    <ThreadRow
                      active={focusedSessionId === session.id}
                      key={session.id}
                      onClick={() => openSession(session)}
                      session={session}
                      working={workingSessionIds.includes(session.id)}
                    />
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>

      <div className="shrink-0 border-t border-(--ui-stroke-quaternary) p-2" data-task-rail-footer="">
        <NavRow
          active={currentView === 'settings'}
          icon="settings-gear"
          label={copy.settings}
          onClick={() => navigate({ id: 'settings', label: copy.settings, icon: NULL_ICON, route: SETTINGS_ROUTE })}
        />
      </div>
    </aside>
  )
}
