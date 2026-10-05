import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router'

import { $activePresetId } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Slot } from '@/contrib/react/slot'
import { registry } from '@/contrib/registry'
import { TASK_CENTER_AREAS } from '@/contrib/task-center'
import { useI18n } from '@/i18n'
import { sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { readKey, writeKey } from '@/lib/storage'
import { useSessionSlice } from '@/lib/use-session-slice'
import { cn } from '@/lib/utils'
import { $desktopActionTasks, buildTaskCenterTasks, type TaskCenterStatus } from '@/store/activity'
import { registerRepoStatusCwd, repoStatusForCwd } from '@/store/coding-status'
import { $backgroundStatusBySession, $statusItemsBySession, stopBackgroundProcess } from '@/store/composer-status'
import { $activeConnectionId } from '@/store/connections'
import { $cronJobs, $cronJobsScope, setCronFocusJobId } from '@/store/cron'
import { applyDesktopLayoutPreset, revealDesktopPane } from '@/store/pane-focus'
import { $previewServerRestart } from '@/store/preview'
import { $profileScope, sidebarProfileForScope } from '@/store/profile'
import { $projectScope, $projectTree, ALL_PROJECTS, projectRootCwd } from '@/store/projects'
import { $approvalRequests } from '@/store/prompts'
import { setRightContextOpen } from '@/store/right-context'
import { $activeSessionId, $currentCwd, $selectedStoredSessionId, $sessions, sessionMatchesStoredId } from '@/store/session'
import { $attentionSessionIds, $sessionStates, $workingSessionIds } from '@/store/session-states'
import { $subagentsBySession } from '@/store/subagents'
import { isAuxiliaryWindow, openSessionInNewWindow } from '@/store/windows'

import { SubagentSection } from '../chat/composer/status-stack/subagent-section'
import { CRON_ROUTE, navigateToWorkspacePage, sessionRoute } from '../routes'
import {
  findLiveTaskRuntimeId,
  findLiveTaskRuntimeIdByStoredId,
  findLiveTaskSession,
  findLiveTaskStoredId,
  resolveTaskWorkspaceCwd
} from '../workspace/task-session'

import { CorePaneTitle } from './core-pane-title'
import { WORKSPACE_OVERVIEW_COPY } from './workspace-overview-copy'

export const WORKSPACE_OVERVIEW_PANE_ID = 'workspace-overview'

const PERSONAL_LAYOUT_VERSION = 3
const PERSONAL_LAYOUT_VERSION_KEY = 'hermes.desktop.personalLayoutVersion'

// One type scale for the whole rail: 13px titles, 12px body, 11px labels and meta.
const TITLE_CLASS = 'truncate text-[0.8125rem] font-medium leading-5 text-(--ui-text-primary)'
const BODY_CLASS = 'text-[0.75rem] leading-[1.125rem] text-(--ui-text-tertiary)'
const META_CLASS = 'text-[0.6875rem] leading-4 text-(--ui-text-quaternary)'

function Section({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[0.6875rem] font-medium leading-4 text-(--ui-text-tertiary)">{title}</h3>
      {children}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 text-[0.75rem] leading-5">
      <dt className="shrink-0 text-(--ui-text-tertiary)">{label}</dt>
      <dd className="min-w-0 truncate text-right text-(--ui-text-secondary)">{value}</dd>
    </div>
  )
}

function IconTile({ className, name, spinning }: { className?: string; name: string; spinning?: boolean }) {
  return (
    <span
      className={cn(
        'flex size-8 shrink-0 items-center justify-center rounded-lg bg-(--ui-bg-quaternary) text-(--ui-text-secondary)',
        className
      )}
    >
      <Codicon name={name} size="0.875rem" spinning={spinning} />
    </span>
  )
}

const WORKSPACE_TOOLS = [
  { icon: 'files', id: 'files' },
  { icon: 'git-compare', id: 'review' },
  { icon: 'terminal', id: 'terminal' }
] as const

function activityIcon(status: TaskCenterStatus): string {
  if (status === 'queued') {
    return 'watch'
  }

  if (status === 'waiting' || status === 'paused' || status === 'interrupted') {
    return 'warning'
  }

  if (status === 'error') {
    return 'error'
  }

  return status === 'success' ? 'pass' : 'loading'
}

export function WorkspaceOverview() {
  const { locale } = useI18n()
  const navigate = useNavigate()
  const copy = WORKSPACE_OVERVIEW_COPY[locale]

  const systemLabels =
    locale === 'zh'
      ? {
          assistantContext: '当前上下文',
          assistantTitle: 'Stardust 助理',
          activeTaskTitle: '当前任务',
          currentConversationTitle: '当前对话',
          assistantSummary: '这里显示当前对话需要的项目、文件、预览和工具上下文；普通聊天不需要项目。',
          assistantSession: '当前对话已经就绪；需要项目、文件或预览时再展开对应能力。',
          taskProgress: '任务进度',
          activity: '任务中心',
          activityRunning: '执行中',
          activityWaiting: '等待你的输入',
          activityQueued: '已排队 / 已计划',
          activityPaused: '已暂停',
          activityInterrupted: '已中断',
          activityCompleted: '已完成',
          activityFailed: '失败',
          durabilityTurn: '当前回合',
          durabilityProcess: '进程内',
          durabilityRestart: '可跨重启',
          openTask: '打开',
          stopTask: '停止',
          manageTask: '管理',
          activityFiles: (count: number) => `${count} 个文件`,
          currentStep: '当前步骤',
          needsInput: '等待你的输入',
          attentionSummary: '当前任务正在等待你的确认或补充信息；工作上下文会保留，回复后可以继续。',
          workingSummary: '有任务仍在执行；可以回到对应会话查看进度，也可以继续处理其他事情。',
          hidePreview: '收起上下文'
        }
      : locale === 'zh-hant'
        ? {
            assistantContext: '目前上下文',
            assistantTitle: 'Stardust 助理',
            activeTaskTitle: '目前任務',
            currentConversationTitle: '目前對話',
            assistantSummary: '這裡顯示目前對話需要的專案、檔案、預覽與工具上下文；一般聊天不需要專案。',
            assistantSession: '目前對話已就緒；需要專案、檔案或預覽時再展開對應能力。',
            taskProgress: '任務進度',
            activity: '任務中心',
            activityRunning: '執行中',
            activityWaiting: '等待你的輸入',
            activityQueued: '已排隊 / 已排程',
            activityPaused: '已暫停',
            activityInterrupted: '已中斷',
            activityCompleted: '已完成',
            activityFailed: '失敗',
            durabilityTurn: '目前回合',
            durabilityProcess: '程序內',
            durabilityRestart: '可跨重啟',
            openTask: '打開',
            stopTask: '停止',
            manageTask: '管理',
            activityFiles: (count: number) => `${count} 個檔案`,
            currentStep: '目前步驟',
            needsInput: '等待你的輸入',
            attentionSummary: '目前任務正在等待你的確認或補充資訊；工作上下文會保留，回覆後可以繼續。',
            workingSummary: '有任務仍在執行；可以回到對應對話查看進度，也可以繼續處理其他事情。',
            hidePreview: '收起上下文'
          }
        : {
            assistantContext: 'Current context',
            assistantTitle: 'Stardust assistant',
            activeTaskTitle: 'Active task',
            currentConversationTitle: 'Current conversation',
            assistantSummary: 'Project, file, preview, and tool context appears here when the current conversation needs it; ordinary chat needs no project.',
            assistantSession: 'The current conversation is ready. Expand project, file, or preview context only when it is useful.',
            taskProgress: 'Task progress',
            activity: 'Task center',
            activityRunning: 'Running',
            activityWaiting: 'Waiting for your input',
            activityQueued: 'Queued / scheduled',
            activityPaused: 'Paused',
            activityInterrupted: 'Interrupted',
            activityCompleted: 'Completed',
            activityFailed: 'Failed',
            durabilityTurn: 'Current turn',
            durabilityProcess: 'Process-local',
            durabilityRestart: 'Restart-durable',
            openTask: 'Open',
            stopTask: 'Stop',
            manageTask: 'Manage',
            activityFiles: (count: number) => `${count} files`,
            currentStep: 'Current step',
            needsInput: 'Waiting for your input',
            attentionSummary: 'The current task is waiting for your input. Its working context is preserved so you can reply and continue.',
            workingSummary: 'A task is still running. Open its conversation to follow progress, or keep working elsewhere.',
            hidePreview: 'Hide context'
          }

  const cwd = useStore($currentCwd)
  const projectScope = useStore($projectScope)
  const projectTree = useStore($projectTree)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const sessions = useStore($sessions)
  const activeSessionId = useStore($activeSessionId)
  const attentionSessionIds = useStore($attentionSessionIds)
  const approvalRequests = useStore($approvalRequests)
  const backgroundStatusBySession = useStore($backgroundStatusBySession)
  const cachedCronJobs = useStore($cronJobs)
  const cronJobsScope = useStore($cronJobsScope)
  const activeConnectionId = useStore($activeConnectionId)
  const profileScope = useStore($profileScope)

  const cronJobs = useMemo(() => (
    cronJobsScope === `${activeConnectionId ?? ''}\u0000${sidebarProfileForScope(profileScope)}` ? cachedCronJobs : []
  ), [activeConnectionId, cachedCronJobs, cronJobsScope, profileScope])

  const desktopActionTasks = useStore($desktopActionTasks)
  const previewServerRestart = useStore($previewServerRestart)
  const sessionStates = useStore($sessionStates)
  const subagentsBySession = useStore($subagentsBySession)
  const workingSessionIds = useStore($workingSessionIds)

  const session = selectedStoredSessionId
    ? sessions.find(candidate => sessionMatchesStoredId(candidate, selectedStoredSessionId))
    : undefined

  const anyAttention = attentionSessionIds.length > 0
  const anyWorking = workingSessionIds.length > 0

  const fallbackTaskSession = selectedStoredSessionId
    ? undefined
    : findLiveTaskSession(sessions, attentionSessionIds, workingSessionIds)

  const fallbackTaskStoredId = selectedStoredSessionId
    ? null
    : findLiveTaskStoredId(sessions, attentionSessionIds, workingSessionIds)

  const scopedProjectCwd =
    projectScope === ALL_PROJECTS ? '' : projectRootCwd(projectTree.find(project => project.id === projectScope))

  const effectiveCwd = resolveTaskWorkspaceCwd(cwd, session, fallbackTaskSession, scopedProjectCwd)
  const repoStatus = useStore(repoStatusForCwd(effectiveCwd))

  const fallbackTaskRuntimeId = fallbackTaskSession
    ? findLiveTaskRuntimeId(sessionStates, fallbackTaskSession)
    : fallbackTaskStoredId
      ? findLiveTaskRuntimeIdByStoredId(sessionStates, fallbackTaskStoredId)
      : null

  const runtimeStoredSessionIds = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(sessionStates).map(([runtimeId, state]) => [runtimeId, state?.storedSessionId ?? null])
      ),
    [sessionStates]
  )

  const statusSessionId = selectedStoredSessionId ? activeSessionId : (fallbackTaskRuntimeId ?? activeSessionId)
  const statusItems = useSessionSlice($statusItemsBySession, statusSessionId)

  const activityTasks = useMemo(
    () =>
      buildTaskCenterTasks({
        actionTasks: desktopActionTasks,
        approvalRequests,
        attentionSessionIds,
        backgroundBySession: backgroundStatusBySession,
        cronJobs,
        previewRestart: previewServerRestart,
        runtimeStoredSessionIds,
        sessions,
        subagentsBySession,
        workingSessionIds
      }),
    [
      approvalRequests,
      attentionSessionIds,
      backgroundStatusBySession,
      cronJobs,
      desktopActionTasks,
      previewServerRestart,
      runtimeStoredSessionIds,
      sessions,
      subagentsBySession,
      workingSessionIds
    ]
  )

  useEffect(() => registerRepoStatusCwd(effectiveCwd), [effectiveCwd])

  const effectiveRepoStatus = repoStatus

  const selectedAttention = selectedStoredSessionId
    ? session
      ? attentionSessionIds.some(storedId => sessionMatchesStoredId(session, storedId))
      : attentionSessionIds.includes(selectedStoredSessionId)
    : false

  const selectedWorking = selectedStoredSessionId
    ? session
      ? workingSessionIds.some(storedId => sessionMatchesStoredId(session, storedId))
      : workingSessionIds.includes(selectedStoredSessionId)
    : false

  const primaryAttention = selectedStoredSessionId ? selectedAttention : anyAttention
  const primaryWorking = selectedStoredSessionId ? selectedWorking : anyWorking
  const displaySession = session ?? fallbackTaskSession

  const displayTaskStoredId =
    displaySession?.id ??
    (primaryAttention || primaryWorking ? (selectedStoredSessionId ?? fallbackTaskStoredId) : fallbackTaskStoredId)

  const sessionLabel = displaySession
    ? storedSessionTitle(displaySession)
    : selectedStoredSessionId
      ? primaryAttention || primaryWorking
        ? systemLabels.activeTaskTitle
        : systemLabels.currentConversationTitle
      : displayTaskStoredId
        ? systemLabels.activeTaskTitle
        : systemLabels.assistantTitle

  const assistantContextSummary = primaryAttention
    ? systemLabels.attentionSummary
    : primaryWorking
      ? systemLabels.workingSummary
      : selectedStoredSessionId
        ? systemLabels.assistantSession
        : systemLabels.assistantSummary

  const normalizedCwd = effectiveCwd.replace(/[/\\]+$/, '')
  const projectName = normalizedCwd.split(/[/\\]/).filter(Boolean).at(-1) ?? copy.noProject
  const branch = effectiveRepoStatus?.branch || copy.noRepository
  const todoItems = statusItems.filter(item => item.type === 'todo')
  const completedTodoCount = todoItems.filter(item => item.todoStatus === 'completed').length
  const activeTodo = todoItems.find(item => item.todoStatus === 'in_progress') ?? todoItems.find(item => item.todoStatus === 'pending')
  const todoPercent = todoItems.length > 0 ? Math.round((completedTodoCount / todoItems.length) * 100) : 0
  const showTaskCard = primaryAttention || primaryWorking

  const summary = primaryAttention
    ? systemLabels.attentionSummary
    : primaryWorking
      ? systemLabels.workingSummary
      : assistantContextSummary

  const currentActivityTaskId = displaySession?.id
    ? `session:${displaySession.id}`
    : displayTaskStoredId
      ? `session:${displayTaskStoredId}`
      : null

  const secondaryActivityTasks = currentActivityTaskId
    ? activityTasks.filter(task => task.id !== currentActivityTaskId)
    : activityTasks

  const activityStatusLabels: Record<TaskCenterStatus, string> = {
    error: systemLabels.activityFailed,
    interrupted: systemLabels.activityInterrupted,
    paused: systemLabels.activityPaused,
    queued: systemLabels.activityQueued,
    running: systemLabels.activityRunning,
    success: systemLabels.activityCompleted,
    waiting: systemLabels.activityWaiting
  }

  const durabilityLabels = {
    'process-local': systemLabels.durabilityProcess,
    'restart-durable': systemLabels.durabilityRestart,
    turn: systemLabels.durabilityTurn
  } as const

  const handleTaskAction = (task: (typeof secondaryActivityTasks)[number]) => {
    if (task.action === 'open-session' && task.sessionId) {
      if (task.rail === 'subagent') {
        void openSessionInNewWindow(task.sessionId, { watch: true })
      } else {
        navigate(sessionRoute(task.sessionId))
      }

      return
    }

    if (task.action === 'stop-process' && task.ownerSessionId && task.processId) {
      void stopBackgroundProcess(task.ownerSessionId, task.processId)

      return
    }

    if (task.action === 'manage-cron') {
      setCronFocusJobId(task.id.slice('cron:'.length))
      navigateToWorkspacePage(navigate, CRON_ROUTE)
    }
  }

  return (
    <aside
      aria-label={copy.workspace}
      className="jarvis-context-rail flex h-full min-h-0 flex-col bg-(--ui-sidebar-surface-background) pt-(--titlebar-height) text-(--ui-text-secondary)"
      data-personal-overview=""
    >
      <header className="flex h-10 shrink-0 items-center justify-between gap-2 px-4" data-context-rail-header="">
        <h2 className="truncate text-[0.8125rem] font-semibold text-(--ui-text-primary)">{copy.workspace}</h2>
        <Button aria-label={systemLabels.hidePreview} onClick={() => setRightContextOpen(false)} size="icon-xs" variant="ghost">
          <Codicon name="close" />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-1">
        <div className="flex flex-col gap-5">
          {activeSessionId && (
            <SubagentSection defaultCollapsed={false} key={activeSessionId} sessionId={activeSessionId} />
          )}

          <Section title={effectiveCwd ? copy.projectContext : systemLabels.assistantContext}>
            {effectiveCwd ? (
              <>
                <div className="flex min-w-0 items-center gap-2.5">
                  <IconTile name="folder" />
                  <div className="min-w-0 flex-1">
                    <div className={TITLE_CLASS}>{projectName}</div>
                    <div className="line-clamp-2 font-mono text-[0.6875rem] leading-4 text-(--ui-text-tertiary) [overflow-wrap:anywhere]">
                      {effectiveCwd}
                    </div>
                  </div>
                </div>
                <dl className="flex flex-col">
                  <Metric label={copy.branch} value={<span className="font-mono">{branch}</span>} />
                  {effectiveRepoStatus && (
                    <Metric label={copy.sync} value={copy.syncValue(effectiveRepoStatus.ahead, effectiveRepoStatus.behind)} />
                  )}
                </dl>
              </>
            ) : (
              <div className="flex min-w-0 items-start gap-2.5">
                <IconTile name="comment" />
                <div className="min-w-0 flex-1">
                  <div className={TITLE_CLASS}>{sessionLabel}</div>
                  <p className={cn('mt-0.5', BODY_CLASS)}>{assistantContextSummary}</p>
                </div>
              </div>
            )}
          </Section>

          {effectiveCwd && (
            <Section title={copy.quickAccess}>
              <div className="grid grid-cols-3 gap-1.5">
                {WORKSPACE_TOOLS.map(tool => (
                  <Button key={tool.id} onClick={() => revealDesktopPane(tool.id)} size="sm" variant="secondary">
                    <Codicon name={tool.icon} />
                    {copy[tool.id]}
                  </Button>
                ))}
              </div>
            </Section>
          )}

          {showTaskCard && (
            <Section title={copy.currentResult}>
              <div className="flex min-w-0 items-start gap-2.5">
                <IconTile
                  className={primaryWorking ? 'text-(--theme-primary)' : undefined}
                  name={primaryAttention ? 'warning' : primaryWorking ? 'loading' : 'target'}
                  spinning={primaryWorking && !primaryAttention}
                />
                <div className="min-w-0 flex-1">
                  <div className={TITLE_CLASS}>{sessionLabel}</div>
                  <p className={cn('mt-0.5', BODY_CLASS)}>{summary}</p>
                  {displaySession?.model && <div className={cn('mt-1 truncate font-mono', META_CLASS)}>{displaySession.model}</div>}
                </div>
              </div>
              {displayTaskStoredId && (primaryAttention || primaryWorking) && (
                <Button className="w-full" onClick={() => navigate(sessionRoute(displayTaskStoredId))} size="sm" variant="secondary">
                  <Codicon name="comment-discussion" />
                  {copy.continueTask}
                </Button>
              )}
              {todoItems.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between gap-2 text-[0.6875rem] leading-4 text-(--ui-text-tertiary)">
                    <span>{systemLabels.taskProgress}</span>
                    <span className="tabular-nums text-(--ui-text-secondary)">
                      {completedTodoCount}/{todoItems.length}
                    </span>
                  </div>
                  <div className="h-1 overflow-hidden rounded-full bg-(--ui-bg-quaternary)">
                    <div
                      className="h-full rounded-full bg-(--theme-primary) transition-[width] duration-200"
                      style={{ width: `${todoPercent}%` }}
                    />
                  </div>
                  {activeTodo && (
                    <p className={cn('line-clamp-2', BODY_CLASS)}>
                      <span className="text-(--ui-text-quaternary)">{systemLabels.currentStep} · </span>
                      <span className="text-(--ui-text-secondary)">{activeTodo.title}</span>
                    </p>
                  )}
                </div>
              )}
            </Section>
          )}

          {secondaryActivityTasks.length > 0 && (
            <Section title={systemLabels.activity}>
              <ul className="-mx-2 flex flex-col">
                {secondaryActivityTasks.slice(0, 10).map(task => (
                  <li
                    className="flex min-w-0 items-start gap-2.5 rounded-lg px-2 py-1.5"
                    data-agent-activity-task=""
                    key={task.id}
                    style={{ paddingLeft: task.depth ? `${0.5 + task.depth * 0.65}rem` : undefined }}
                  >
                    <Codicon
                      className={cn(
                        'mt-1 shrink-0',
                        task.status === 'running'
                          ? 'text-(--theme-primary)'
                          : task.status === 'waiting' || task.status === 'error' || task.status === 'interrupted'
                            ? 'text-(--ui-text-secondary)'
                            : 'text-(--ui-text-tertiary)'
                      )}
                      name={activityIcon(task.status)}
                      size="0.8125rem"
                      spinning={task.status === 'running'}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[0.75rem] font-medium leading-5 text-(--ui-text-primary)">
                        {task.label}
                      </div>
                      {task.detail && <div className={cn('line-clamp-2 text-(--ui-text-tertiary)', META_CLASS)}>{task.detail}</div>}
                      <div className={cn('mt-0.5 flex min-w-0 items-center gap-1', META_CLASS)}>
                        <span>{activityStatusLabels[task.status]}</span>
                        {task.durability && (
                          <>
                            <span aria-hidden>·</span>
                            <span>{durabilityLabels[task.durability]}</span>
                          </>
                        )}
                        {task.artifactRefs?.length ? (
                          <>
                            <span aria-hidden>·</span>
                            <span>{systemLabels.activityFiles(task.artifactRefs.length)}</span>
                          </>
                        ) : null}
                      </div>
                    </div>
                    {task.action && (
                      <Button onClick={() => handleTaskAction(task)} size="xs" variant="ghost">
                        {task.action === 'stop-process'
                          ? systemLabels.stopTask
                          : task.action === 'manage-cron'
                            ? systemLabels.manageTask
                            : systemLabels.openTask}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Slot area={TASK_CENTER_AREAS.sections} />
        </div>
      </div>
    </aside>
  )
}

export function schedulePersonalLayoutMigration(): void {
  // Helper windows share the primary window's storage origin. Letting one of
  // them consume this version marker would make the real desktop skip its
  // one-time product-layout migration later.
  if (isAuxiliaryWindow()) {
    return
  }

  const current = Number(readKey(PERSONAL_LAYOUT_VERSION_KEY) ?? 0)

  if (Number.isFinite(current) && current >= PERSONAL_LAYOUT_VERSION) {
    return
  }

  queueMicrotask(() => {
    // Re-check after module initialization: the layout registry and default tree
    // are wired synchronously by controller.tsx before this microtask runs.
    if ($activePresetId.get() === 'default') {
      applyDesktopLayoutPreset('default')
    }

    setRightContextOpen(false)
    writeKey(PERSONAL_LAYOUT_VERSION_KEY, String(PERSONAL_LAYOUT_VERSION))
  })
}

export function registerWorkspaceOverviewPane(): () => void {
  return registry.register({
    id: WORKSPACE_OVERVIEW_PANE_ID,
    area: 'panes',
    source: 'core',
    title: 'overview',
    data: {
      placement: 'right',
      collapsible: true,
      uncloseable: true,
      tabTitle: () => <CorePaneTitle id="overview" />,
      revealAliases: ['overview', 'workspace-overview'],
      width: '340px',
      minWidth: '300px',
      maxWidth: '480px'
    },
    render: () => <WorkspaceOverview />
  })
}
