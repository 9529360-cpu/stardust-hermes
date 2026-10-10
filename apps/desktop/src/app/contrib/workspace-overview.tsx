import type { WorkCancelResult, WorkItem, WorkListResult } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'
import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { openAgentTerminal } from '@/app/right-sidebar/terminal/terminals'
import { PrChecksBadge } from '@/components/chat/pr-checks-badge'
import { $activePresetId } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { EmptyState } from '@/components/ui/empty-state'
import { RowButton } from '@/components/ui/row-button'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Textarea } from '@/components/ui/textarea'
import { Slot } from '@/contrib/react/slot'
import { registry } from '@/contrib/registry'
import { TASK_CENTER_AREAS } from '@/contrib/task-center'
import type { HermesBranchPullRequest } from '@/global'
import { getCronSuggestions } from '@/hermes'
import { useI18n } from '@/i18n'
import { sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { readKey, writeKey } from '@/lib/storage'
import { useSessionSlice } from '@/lib/use-session-slice'
import { cn } from '@/lib/utils'
import {
  $desktopActionTasks,
  buildTaskCenterTasks,
  filterTaskCenterTasks,
  type TaskCenterStatus,
  type TaskCenterTask,
  type TaskCenterView
} from '@/store/activity'
import { $clarifyRequests, answerClarifyRequest } from '@/store/clarify'
import { $backgroundStatusBySession, $statusItemsBySession, stopBackgroundProcess } from '@/store/composer-status'
import { $activeConnectionId } from '@/store/connections'
import { $cronJobs, $cronJobsScope, setCronFocusJobId } from '@/store/cron'
import { $gateway } from '@/store/gateway'
import { applyDesktopLayoutPreset, revealDesktopPane } from '@/store/pane-focus'
import { $previewServerRestart } from '@/store/preview'
import {
  $activeGatewayProfile,
  $profileScope,
  ALL_PROFILES,
  normalizeProfileKey,
  sidebarProfileForScope
} from '@/store/profile'
import { $projectScope, $projectTree, ALL_PROJECTS, projectRootCwd } from '@/store/projects'
import { $approvalRequests, answerApproval } from '@/store/prompts'
import {
  $pullRequestChecksByPr,
  $pullRequestsByBranch,
  pullRequestChecksKey,
  type PullRequestChecksState,
  sessionPrKey
} from '@/store/pull-requests'
import { setRightContextOpen } from '@/store/right-context'
import {
  $activeSessionId,
  $currentCwd,
  $selectedStoredSessionId,
  $sessions,
  sessionMatchesStoredId
} from '@/store/session'
import { $attentionSessionIds, $sessionStates, $workingSessionIds } from '@/store/session-states'
import { $subagentsBySession } from '@/store/subagents'
import { isAuxiliaryWindow, openSessionInNewWindow } from '@/store/windows'
import type { SessionInfo } from '@/types/hermes'

import { SubagentSection } from '../chat/composer/status-stack/subagent-section'
import { openSession } from '../open-session'
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
const WORK_POLL_MS = 3000

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

function taskPullRequestChecksState(
  task: TaskCenterTask,
  sessions: readonly SessionInfo[],
  pullRequestsByBranch: Record<string, HermesBranchPullRequest>,
  pullRequestChecksByPr: Record<string, PullRequestChecksState>
) {
  const taskSessionId = task.sessionId

  if (!taskSessionId) {
    return undefined
  }

  const session = sessions.find(candidate => sessionMatchesStoredId(candidate, taskSessionId))
  const prKey = session ? sessionPrKey(session) : null
  const pr = prKey ? pullRequestsByBranch[prKey] : undefined
  const checksKey = prKey && pr ? pullRequestChecksKey(prKey, pr.number) : null

  return checksKey ? (pullRequestChecksByPr[checksKey] ?? 'loading') : undefined
}

function TaskPullRequestChecksBadge({
  pullRequestChecksByPr,
  pullRequestsByBranch,
  sessions,
  task
}: {
  pullRequestChecksByPr: Record<string, PullRequestChecksState>
  pullRequestsByBranch: Record<string, HermesBranchPullRequest>
  sessions: readonly SessionInfo[]
  task: TaskCenterTask
}) {
  const state = taskPullRequestChecksState(task, sessions, pullRequestsByBranch, pullRequestChecksByPr)

  return state ? <PrChecksBadge compact state={state} /> : null
}

function workStatusIcon(status: WorkItem['status']): string {
  if (status === 'running') {
    return 'loading'
  }

  if (status === 'failed' || status === 'interrupted') {
    return 'warning'
  }

  if (status === 'completed') {
    return 'pass'
  }

  return 'circle-slash'
}

export function WorkLedgerSection({
  items,
  labels,
  onCancel
}: {
  items: WorkItem[]
  labels: {
    cancel: string
    empty: string
    kinds: Record<WorkItem['kind'], string>
    statuses: Record<WorkItem['status'], string>
    title: string
  }
  onCancel: (item: WorkItem) => void
}) {
  return (
    <Section title={labels.title}>
      {items.length === 0 ? (
        <div className={cn(META_CLASS, 'py-1')}>{labels.empty}</div>
      ) : (
        <ul className="-mx-2 flex flex-col">
          {items.map(item => (
            <li className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1.5" key={item.id}>
              <Codicon
                className={item.status === 'running' ? 'text-(--theme-primary)' : 'text-(--ui-text-tertiary)'}
                name={workStatusIcon(item.status)}
                size="0.8125rem"
                spinning={item.status === 'running'}
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[0.75rem] font-medium leading-5 text-(--ui-text-primary)">
                  {item.title}
                </div>
                <div className={META_CLASS}>
                  {labels.kinds[item.kind]} · {labels.statuses[item.status]}
                </div>
              </div>
              {item.status === 'running' && item.kind === 'subagent' && (
                <Button aria-label={`${labels.cancel}: ${item.title}`} onClick={() => onCancel(item)} size="icon-xs" variant="ghost">
                  <Codicon name="debug-stop" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function taskActionLabel(
  task: TaskCenterTask,
  labels: {
    manageTask: string
    openBesideTask: string
    openTask: string
    reviewTask: string
    stopTask: string
  }
): string {
  if (task.action === 'stop-process') {
    return labels.stopTask
  }

  if (task.action === 'manage-cron') {
    return labels.manageTask
  }

  if (task.action === 'review-cron-suggestion') {
    return labels.reviewTask
  }

  return task.action === 'open-session' && task.rail !== 'subagent' ? labels.openBesideTask : labels.openTask
}

interface TaskCenterDetailProps {
  activityStatusLabel: string
  actionLabel?: string
  durabilityLabel?: string
  onAction?: () => void
  task: TaskCenterTask
  labels: {
    taskArtifacts: string
    taskBranch: string
    taskContext: string
    taskDetails: string
    taskFolder: string
    taskLifetime: string
    taskProcess: string
    taskProject: string
    taskSession: string
    taskWorktree: string
  }
}

function TaskCenterDetail({
  activityStatusLabel,
  actionLabel,
  durabilityLabel,
  labels,
  onAction,
  task
}: TaskCenterDetailProps) {
  const contextRows = [
    task.sessionId || task.ownerSessionId
      ? { label: labels.taskSession, value: task.sessionId ?? task.ownerSessionId }
      : null,
    task.workspace?.project ? { label: labels.taskProject, value: task.workspace.project } : null,
    task.workspace?.worktree ? { label: labels.taskWorktree, value: task.workspace.worktree } : null,
    task.workspace?.branch ? { label: labels.taskBranch, value: task.workspace.branch } : null,
    task.workspace?.cwd ? { label: labels.taskFolder, value: task.workspace.cwd } : null,
    task.processId ? { label: labels.taskProcess, value: task.processId } : null,
    durabilityLabel ? { label: labels.taskLifetime, value: durabilityLabel } : null
  ].filter((row): row is { label: string; value: string } => Boolean(row))

  return (
    <div className="mt-2 border-t border-(--ui-stroke-tertiary) pt-2" data-task-center-detail={task.id}
      data-testid="task-center-detail">
      <div className={cn('mb-2', META_CLASS)}>{labels.taskDetails}</div>
      <div className="flex items-start gap-2">
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
          <div className="text-[0.75rem] font-medium leading-5 text-(--ui-text-primary)">{task.label}</div>
          <div className={META_CLASS}>
            {activityStatusLabel}
            {durabilityLabel && (
              <>
                <span aria-hidden> · </span>
                {durabilityLabel}
              </>
            )}
          </div>
        </div>
      </div>

      {task.detail && <p className={cn('mt-2 whitespace-pre-wrap break-words', BODY_CLASS)}>{task.detail}</p>}

      {contextRows.length > 0 && (
        <div className="mt-3">
          <div className={cn('mb-1', META_CLASS)}>{labels.taskContext}</div>
          <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-[0.6875rem] leading-4">
            {contextRows.map(row => (
              <div className="contents" key={row.label}>
                <dt className="truncate text-(--ui-text-quaternary)">{row.label}</dt>
                <dd className="min-w-0 break-all text-(--ui-text-secondary)">{row.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {task.artifactRefs && task.artifactRefs.length > 0 && (
        <div className="mt-3">
          <div className={cn('mb-1', META_CLASS)}>{labels.taskArtifacts}</div>
          <ul className="space-y-0.5 text-[0.6875rem] leading-4 text-(--ui-text-secondary)">
            {task.artifactRefs.map(ref => (
              <li className="break-all" key={ref}>
                {ref}
              </li>
            ))}
          </ul>
        </div>
      )}

      {actionLabel && onAction && (
        <Button className="mt-3" onClick={onAction} size="xs" variant="secondary">
          {actionLabel}
        </Button>
      )}
    </div>
  )
}

function UnifiedInputCard({ task, gateway }: { task: TaskCenterTask; gateway: Parameters<typeof answerApproval>[0] }) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const request = task.approvalRequest
  const clarify = task.clarifyRequest

  if (!request && !clarify) {
    return null
  }

  const choices = clarify?.questions?.[0]?.choices ?? clarify?.choices ?? []

  const submit = async (answer: string) => {
    if (busy || !answer.trim()) {return}
    setBusy(true)

    if (request) {
      await answerApproval(gateway, request, answer)
    } else if (clarify) {
      answerClarifyRequest(clarify, answer)
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-primary/25 bg-primary/5 p-2" data-task-center-input-card="">
      <div className="mb-1 text-[0.75rem] font-medium text-primary">{task.label}</div>
      <div className={cn('mb-2 line-clamp-3', BODY_CLASS)}>{task.detail}</div>
      <div className="flex flex-wrap gap-1.5">
        {request ? (
          <>
            <Button disabled={busy} onClick={() => void submit('once')} size="xs" variant="secondary">
              Run
            </Button>
            <Button disabled={busy} onClick={() => void submit('deny')} size="xs" variant="ghost">
              Reject
            </Button>
          </>
        ) : (
          choices.map(choice => (
            <Button disabled={busy} key={choice} onClick={() => void submit(choice)} size="xs" variant="secondary">
              {choice}
            </Button>
          ))
        )}
      </div>
      {clarify && choices.length === 0 && (
        <div className="mt-1.5 flex gap-1.5">
          <Textarea
            className="min-h-8"
            onChange={event => setDraft(event.target.value)}
            placeholder="Your answer"
            value={draft}
          />
          <Button disabled={busy || !draft.trim()} onClick={() => void submit(draft)} size="xs" variant="secondary">
            Send
          </Button>
        </div>
      )}
    </div>
  )
}

export function WorkspaceOverview() {
  const { locale } = useI18n()
  const navigate = useNavigate()
  const copy = WORKSPACE_OVERVIEW_COPY[locale]
  const [taskCenterView, setTaskCenterView] = useState<TaskCenterView>('all')
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [workItems, setWorkItems] = useState<WorkItem[]>([])

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
          taskDetails: '任务详情',
          taskContext: '任务上下文',
          taskFolder: '文件夹',
          taskLifetime: '生命周期',
          taskProcess: '进程',
          taskArtifacts: '产物文件',
          taskBranch: '分支',
          taskProject: '项目',
          taskSession: '会话',
          taskWorktree: '工作树',
          openTask: '打开',
          openBesideTask: '在旁边打开',
          reviewTask: '查看',
          stopTask: '停止',
          manageTask: '管理',
          activityFiles: (count: number) => `${count} 个文件`,
          currentStep: '当前步骤',
          needsInput: '等待你的输入',
          attentionSummary: '当前任务正在等待你的确认或补充信息；工作上下文会保留，回复后可以继续。',
          workingSummary: '有任务仍在执行；可以回到对应会话查看进度，也可以继续处理其他事情。',
          hidePreview: '收起上下文',
          emptyRail: '后台任务、子代理和进度会显示在这里。',
          workTitle: '后台工作',
          workEmpty: '当前会话没有后台工作。',
          workCancel: '停止工作',
          workKindCron: '定时任务',
          workKindDelegation: '委派任务',
          workKindProcess: '后台进程',
          workKindSubagent: '子代理',
          workStatusCancelled: '已取消'
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
            taskDetails: '任務詳情',
            taskContext: '任務上下文',
            taskFolder: '資料夾',
            taskLifetime: '生命週期',
            taskProcess: '程序',
            taskArtifacts: '產出檔案',
            taskBranch: '分支',
            taskProject: '專案',
            taskSession: '工作階段',
            taskWorktree: '工作樹',
            openTask: '打開',
            openBesideTask: '在旁邊開啟',
            reviewTask: '檢視',
            stopTask: '停止',
            manageTask: '管理',
            activityFiles: (count: number) => `${count} 個檔案`,
            currentStep: '目前步驟',
            needsInput: '等待你的輸入',
            attentionSummary: '目前任務正在等待你的確認或補充資訊；工作上下文會保留，回覆後可以繼續。',
            workingSummary: '有任務仍在執行；可以回到對應對話查看進度，也可以繼續處理其他事情。',
            hidePreview: '收起上下文',
            emptyRail: '背景任務、子代理與進度會顯示在這裡。',
            workTitle: '背景工作',
            workEmpty: '目前工作階段沒有背景工作。',
            workCancel: '停止工作',
            workKindCron: '排程任務',
            workKindDelegation: '委派任務',
            workKindProcess: '背景程序',
            workKindSubagent: '子代理',
            workStatusCancelled: '已取消'
          }
        : {
            assistantContext: 'Current context',
            assistantTitle: 'Stardust assistant',
            activeTaskTitle: 'Active task',
            currentConversationTitle: 'Current conversation',
            assistantSummary:
              'Project, file, preview, and tool context appears here when the current conversation needs it; ordinary chat needs no project.',
            assistantSession:
              'The current conversation is ready. Expand project, file, or preview context only when it is useful.',
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
            taskDetails: 'Task details',
            taskContext: 'Task context',
            taskFolder: 'Folder',
            taskLifetime: 'Lifetime',
            taskProcess: 'Process',
            taskArtifacts: 'Files',
            taskBranch: 'Branch',
            taskProject: 'Project',
            taskSession: 'Session',
            taskWorktree: 'Worktree',
            openTask: 'Open',
            openBesideTask: 'Open beside',
            reviewTask: 'Review',
            stopTask: 'Stop',
            manageTask: 'Manage',
            activityFiles: (count: number) => `${count} files`,
            currentStep: 'Current step',
            needsInput: 'Waiting for your input',
            attentionSummary:
              'The current task is waiting for your input. Its working context is preserved so you can reply and continue.',
            workingSummary:
              'A task is still running. Open its conversation to follow progress, or keep working elsewhere.',
            hidePreview: 'Hide context',
            emptyRail: 'Background tasks, subagents and progress show up here.',
            workTitle: 'Background work',
            workEmpty: 'No background work for this session.',
            workCancel: 'Stop work',
            workKindCron: 'Scheduled run',
            workKindDelegation: 'Delegation',
            workKindProcess: 'Background process',
            workKindSubagent: 'Subagent',
            workStatusCancelled: 'Cancelled'
          }

  const cwd = useStore($currentCwd)
  const projectScope = useStore($projectScope)
  const projectTree = useStore($projectTree)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const sessions = useStore($sessions)
  const activeSessionId = useStore($activeSessionId)
  const attentionSessionIds = useStore($attentionSessionIds)
  const approvalRequests = useStore($approvalRequests)
  const clarifyRequests = useStore($clarifyRequests)
  const gateway = useStore($gateway)
  const { requestGateway } = useGatewayRequest()
  const backgroundStatusBySession = useStore($backgroundStatusBySession)
  const cachedCronJobs = useStore($cronJobs)
  const cronJobsScope = useStore($cronJobsScope)
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const activeConnectionId = useStore($activeConnectionId)
  const profileScope = useStore($profileScope)

  const cronJobs = useMemo(
    () =>
      cronJobsScope === `${activeConnectionId ?? ''}\u0000${sidebarProfileForScope(profileScope)}`
        ? cachedCronJobs
        : [],
    [activeConnectionId, cachedCronJobs, cronJobsScope, profileScope]
  )

  const suggestionProfile =
    profileScope === ALL_PROFILES ? normalizeProfileKey(activeGatewayProfile) : normalizeProfileKey(profileScope)

  const cronSuggestionsQuery = useQuery({
    queryKey: ['cron-suggestions', activeConnectionId || 'local', suggestionProfile],
    queryFn: () => getCronSuggestions(suggestionProfile),
    retry: false
  })

  const cronSuggestions = cronSuggestionsQuery.data

  const desktopActionTasks = useStore($desktopActionTasks)
  const pullRequestsByBranch = useStore($pullRequestsByBranch)
  const pullRequestChecksByPr = useStore($pullRequestChecksByPr)
  const previewServerRestart = useStore($previewServerRestart)
  const sessionStates = useStore($sessionStates)
  const subagentsBySession = useStore($subagentsBySession)
  const workingSessionIds = useStore($workingSessionIds)

  const currentSession = selectedStoredSessionId
    ? sessions.find(candidate => sessionMatchesStoredId(candidate, selectedStoredSessionId))
    : sessions.find(candidate => candidate.id === activeSessionId)

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

  const effectiveCwd = resolveTaskWorkspaceCwd(cwd, session ?? currentSession, fallbackTaskSession, scopedProjectCwd)

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
        clarifyRequests,
        attentionSessionIds,
        backgroundBySession: backgroundStatusBySession,
        cronJobs,
        cronSuggestions,
        previewRestart: previewServerRestart,
        projectTree,
        runtimeStoredSessionIds,
        sessions,
        subagentsBySession,
        workingSessionIds
      }),
    [
      approvalRequests,
      clarifyRequests,
      attentionSessionIds,
      backgroundStatusBySession,
      cronJobs,
      cronSuggestions,
      desktopActionTasks,
      previewServerRestart,
      projectTree,
      runtimeStoredSessionIds,
      sessions,
      subagentsBySession,
      workingSessionIds
    ]
  )

  const taskCenterCheckStatuses = useMemo(() => {
    const statuses: Record<string, PullRequestChecksState> = {}

    for (const task of activityTasks) {
      const state = taskPullRequestChecksState(task, sessions, pullRequestsByBranch, pullRequestChecksByPr)

      if (state) {
        statuses[task.id] = state
      }
    }

    return statuses
  }, [activityTasks, pullRequestChecksByPr, pullRequestsByBranch, sessions])

  const filteredActivityTasks = useMemo(
    () => filterTaskCenterTasks(activityTasks, taskCenterView, taskCenterCheckStatuses),
    [activityTasks, taskCenterCheckStatuses, taskCenterView]
  )

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

  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | null = null

    // Clear immediately so the previous session's work cannot remain visible while
    // the new session's ownership-scoped request is in flight.
    setWorkItems([])

    const loadWork = async (): Promise<void> => {
      if (!activeSessionId || !gateway) {
        return
      }

      try {
        const result = await requestGateway<WorkListResult>('work.list', { session_id: activeSessionId })

        if (disposed) {
          return
        }

        const next = result.work ?? []
        // Keep polling while the session is active so work that starts later shows up
        // without a session switch. An unchanged snapshot keeps the previous array.
        setWorkItems(current => (JSON.stringify(current) === JSON.stringify(next) ? current : next))
        timer = setTimeout(() => void loadWork(), WORK_POLL_MS)
      } catch {
        if (!disposed) {
          setWorkItems([])
        }
      }
    }

    void loadWork()

    return () => {
      disposed = true

      if (timer !== null) {
        clearTimeout(timer)
      }
    }
  }, [activeSessionId, gateway, requestGateway])

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

  const todoItems = statusItems.filter(item => item.type === 'todo')
  const completedTodoCount = todoItems.filter(item => item.todoStatus === 'completed').length

  const activeTodo =
    todoItems.find(item => item.todoStatus === 'in_progress') ?? todoItems.find(item => item.todoStatus === 'pending')

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

  const secondaryActivityTasks =
    taskCenterView === 'needs-attention'
      ? filteredActivityTasks
      : currentActivityTaskId
        ? filteredActivityTasks.filter(task => task.id !== currentActivityTaskId)
        : filteredActivityTasks

  const visibleActivityTasks = secondaryActivityTasks.slice(0, 10)
  const selectedTask = visibleActivityTasks.find(task => task.id === selectedTaskId)

  useEffect(() => {
    if (selectedTaskId && !selectedTask) {
      setSelectedTaskId(null)
    }
  }, [selectedTask, selectedTaskId])

  const showTaskCenterSection =
    secondaryActivityTasks.length > 0 || (taskCenterView === 'needs-attention' && activityTasks.length > 0)

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

  const workKindLabels: Record<WorkItem['kind'], string> = {
    cron: systemLabels.workKindCron,
    delegation: systemLabels.workKindDelegation,
    process: systemLabels.workKindProcess,
    subagent: systemLabels.workKindSubagent
  }

  const workStatusLabels: Record<WorkItem['status'], string> = {
    cancelled: systemLabels.workStatusCancelled,
    completed: systemLabels.activityCompleted,
    failed: systemLabels.activityFailed,
    interrupted: systemLabels.activityInterrupted,
    running: systemLabels.activityRunning
  }

  const handleTaskAction = (task: TaskCenterTask) => {
    if (task.action === 'open-session' && task.sessionId) {
      if (task.rail === 'subagent') {
        void openSessionInNewWindow(task.sessionId, { watch: true })
      } else {
        openSession(task.sessionId, navigate, 'stack')
      }

      return
    }

    if (task.action === 'review-cron-suggestion') {
      navigateToWorkspacePage(navigate, CRON_ROUTE)

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
        <Button
          aria-label={systemLabels.hidePreview}
          onClick={() => setRightContextOpen(false)}
          size="icon-xs"
          variant="ghost"
        >
          <Codicon name="close" />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-1">
        <div className="flex flex-col gap-5">
          {activeSessionId && (
            <SubagentSection defaultCollapsed={false} key={activeSessionId} sessionId={activeSessionId} />
          )}

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
                  {displaySession?.model && (
                    <div className={cn('mt-1 truncate font-mono', META_CLASS)}>{displaySession.model}</div>
                  )}
                  {effectiveCwd && <div className={cn('mt-1 truncate font-mono', META_CLASS)}>{effectiveCwd}</div>}
                </div>
              </div>
              {displayTaskStoredId && (primaryAttention || primaryWorking) && (
                <Button
                  className="w-full"
                  onClick={() => navigate(sessionRoute(displayTaskStoredId))}
                  size="sm"
                  variant="secondary"
                >
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

          {activeSessionId && (
            <WorkLedgerSection
              items={workItems}
              labels={{
                cancel: systemLabels.workCancel,
                empty: systemLabels.workEmpty,
                kinds: workKindLabels,
                statuses: workStatusLabels,
                title: systemLabels.workTitle
              }}
              onCancel={item => {
                void requestGateway<WorkCancelResult>('work.cancel', { id: item.id, session_id: activeSessionId })
                  .then(result => {
                    if (result.status === 'interrupt_requested' || result.status === 'cancelled' || result.status === 'already_finished') {
                      setWorkItems(current => current.filter(candidate => candidate.id !== item.id))
                    }
                  })
                  .catch(() => undefined)
              }}
            />
          )}

          {showTaskCenterSection && (
            <Section title={taskCenterView === 'needs-attention' ? copy.reviewQueue : systemLabels.activity}>
              <div className="flex items-center justify-between gap-2" data-task-center-view={taskCenterView}>
                <span className={META_CLASS}>{copy.taskCenterView}</span>
                <SegmentedControl
                  onChange={setTaskCenterView}
                  options={[
                    { id: 'all', label: copy.taskCenterAll },
                    { id: 'needs-attention', label: copy.needsAttention }
                  ]}
                  value={taskCenterView}
                />
              </div>
              {secondaryActivityTasks.length > 0 ? (
                <ul className="-mx-2 flex flex-col">
                {visibleActivityTasks.map(task => (
                  <li
                    className={cn(
                      'flex min-w-0 items-start gap-2.5 rounded-lg px-2 py-1.5',
                      selectedTaskId === task.id && 'bg-(--ui-bg-quaternary)'
                    )}
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
                      <RowButton
                        aria-expanded={selectedTaskId === task.id}
                        className="-mx-1 w-full min-w-0 px-1 text-left"
                        onClick={() => setSelectedTaskId(current => (current === task.id ? null : task.id))}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[0.75rem] font-medium leading-5 text-(--ui-text-primary)">
                            {task.label}
                          </div>
                        </div>
                      </RowButton>
                      <TaskPullRequestChecksBadge
                        pullRequestChecksByPr={pullRequestChecksByPr}
                        pullRequestsByBranch={pullRequestsByBranch}
                        sessions={sessions}
                        task={task}
                      />
                      {task.testResult && (
                        <div
                          className={cn(
                            'mt-1 rounded-md border px-2 py-1.5',
                            task.testResult.status === 'failed'
                              ? 'border-destructive/30 bg-destructive/5'
                              : task.testResult.status === 'passed'
                                ? 'border-emerald-500/25 bg-emerald-500/5'
                                : 'border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary)'
                          )}
                          data-test-result-card=""
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-[0.6875rem] font-medium">{task.testResult.command}</span>
                            <span className="shrink-0 text-[0.625rem] text-(--ui-text-tertiary)">
                              {task.testResult.status === 'running'
                                ? activityStatusLabels.running
                                : task.testResult.status === 'passed'
                                  ? activityStatusLabels.success
                                  : activityStatusLabels.error}
                            </span>
                          </div>
                          {typeof task.testResult.exitCode === 'number' && (
                            <div className="mt-0.5 text-[0.625rem] text-(--ui-text-tertiary)">
                              exit {task.testResult.exitCode}
                            </div>
                          )}
                          <Button
                            className="mt-1 h-6 px-1.5 text-[0.625rem]"
                            onClick={() => openAgentTerminal(task.processId ?? '', task.label)}
                            size="xs"
                            variant="ghost"
                          >
                            <Codicon name="terminal" />
                            {systemLabels.openTask}
                          </Button>
                        </div>
                      )}
                      {task.detail && (
                        <div className={cn('line-clamp-2 text-(--ui-text-tertiary)', META_CLASS)}>{task.detail}</div>
                      )}
                      {task.workspace && (
                        <div className={cn('truncate text-(--ui-text-quaternary)', META_CLASS)}>
                          {[task.workspace.project, task.workspace.worktree, task.workspace.branch, task.workspace.cwd]
                            .filter(Boolean)
                            .join(' · ')}
                        </div>
                      )}
                      {selectedTaskId === task.id && (
                        <TaskCenterDetail
                          actionLabel={task.action ? taskActionLabel(task, systemLabels) : undefined}
                          activityStatusLabel={activityStatusLabels[task.status]}
                          durabilityLabel={task.durability ? durabilityLabels[task.durability] : undefined}
                          labels={systemLabels}
                          onAction={task.action ? () => handleTaskAction(task) : undefined}
                          task={task}
                        />
                      )}
                    </div>
                    {task.clarifyRequest || task.approvalRequest ? (
                      <UnifiedInputCard gateway={gateway} task={task} />
                    ) : null}
                    {task.action && (
                      <Button onClick={() => handleTaskAction(task)} size="xs" variant="ghost">
                        {task.action === 'stop-process'
                          ? systemLabels.stopTask
                          : task.action === 'manage-cron'
                            ? systemLabels.manageTask
                            : task.action === 'review-cron-suggestion'
                              ? systemLabels.reviewTask
                              : task.action === 'open-session' && task.rail !== 'subagent'
                                ? systemLabels.openBesideTask
                                : systemLabels.openTask}
                      </Button>
                    )}
                  </li>
                ))}
                </ul>
              ) : taskCenterView === 'needs-attention' ? (
                <EmptyState description={copy.noNeedsAttention} title={copy.needsAttention} />
              ) : null}
            </Section>
          )}

          <Slot area={TASK_CENTER_AREAS.sections} />

          {!effectiveCwd && !showTaskCard && secondaryActivityTasks.length === 0 && (
            <EmptyState description={systemLabels.emptyRail} title={copy.nothingPending} />
          )}
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
