import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect } from 'react'
import { useNavigate } from 'react-router'

import { revealTreePane } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { useSessionSlice, useStoreSelector } from '@/lib/use-session-slice'
import { registerRepoStatusCwd, repoStatusForCwd } from '@/store/coding-status'
import { $statusItemsBySession, type ComposerStatusItem } from '@/store/composer-status'
import { openCommandPalette } from '@/store/command-palette'
import { revealDesktopPane } from '@/store/pane-focus'
import { $previewTarget } from '@/store/preview'
import { setSidebarOpen } from '@/store/layout'
import { $projectScope, $projectTree, ALL_PROJECTS, openFolderAsProject, projectRootCwd } from '@/store/projects'
import { openReviewForPath } from '@/store/review'
import { setRightContextOpen } from '@/store/right-context'
import {
  $activeSessionId,
  $currentCwd,
  $gatewayState,
  $selectedStoredSessionId,
  $sessions,
  sessionMatchesStoredId
} from '@/store/session'
import { $attentionSessionIds, $sessionStates, $workingSessionIds } from '@/store/session-states'

import { sessionRoute } from '../routes'
import {
  findLiveTaskRuntimeId,
  findLiveTaskRuntimeIdByStoredId,
  findLiveTaskSession,
  findLiveTaskStoredId,
  resolveTaskWorkspaceCwd
} from './task-session'
import './workspace.css'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

const EN = {
  eyebrow: 'Your AI workspace',
  headline: 'I’m here. What should we handle first?',
  bodyWorking: 'I’m following the active task and keeping its working context visible here.',
  bodyNeedsInput: 'A task is waiting for your input. I’ll keep its context ready so you can answer and continue.',
  bodySession: 'Continue the current conversation, or attach a project when the work needs code and files.',
  bodyIdle: 'Start with a question, a task, or everyday work. Add a project only when you need the coding workspace.',
  continueTask: 'Continue working',
  startChat: 'Start a conversation',
  assistantReadyTitle: 'Stardust assistant',
  activeTaskTitle: 'Active task',
  currentConversationTitle: 'Current conversation',
  continueConversation: 'Continue conversation',
  chooseProject: 'Choose a project',
  projectTools: 'Choose a project to enable coding tools',
  assistantMode: 'Assistant mode',
  assistantActions: 'Assistant shortcuts',
  assistantActionsBody: 'Ask a question, continue the current conversation, or open your tasks without attaching a code project.',
  codingWorkspace: 'Coding workspace',
  codingWorkspaceBody: 'Choose a project when you want files, Git review, terminal context, and live preview to join the same workspace.',
  gitDiffLabel: 'Git Diff',
  filesLabel: 'Files',
  previewLabel: 'Preview',
  openTasks: 'View tasks',
  agentWorking: 'Agent running',
  agentNeedsInput: 'Agent needs your input',
  agentReady: 'Agent ready',
  environment: 'Workspace',
  changes: 'Changes',
  phase: 'Current phase',
  context: 'Task context',
  contextIdle: 'Workspace readiness',
  activity: 'Agent activity',
  activityIdle: 'Current status',
  gitChanges: 'Code changes (Git Diff)',
  preview: 'Live preview',
  branch: 'Branch',
  filesChanged: 'files changed',
  clean: 'Working tree clean',
  review: 'Review changes',
  terminal: 'Open terminal',
  files: 'Open files',
  noProject: 'No project selected',
  noRepo: 'No Git repository',
  noPreview: 'No preview is open yet',
  openPreview: 'Open preview',
  noChanges: 'No local changes to review',
  hiddenChanges: 'The repository has changes outside this preview. Open Review to inspect all changes.',
  moreChanges: 'more changes in Review',
  working: 'Running',
  needsInput: 'Waiting for your input',
  ready: 'Ready',
  connected: 'Gateway connected',
  connecting: 'Gateway connecting',
  next: 'Next',
  pending: 'Pending',
  completed: 'Completed',
  cancelled: 'Cancelled',
  inProgress: 'In progress',
  failed: 'Failed',
  noActiveWork: 'No active task activity',
  command: 'Tell me what you want to do, or open the conversation…',
  topCommand: 'Search commands, pages, and actions…',
  moreActions: 'More actions'
}

const ZH = {
  eyebrow: '你的 AI 工作空间',
  headline: '我在。有什么需要我处理的吗？',
  bodyWorking: '有任务正在执行，我会把可用的任务进度、工作上下文和结果持续放在这里。',
  bodyNeedsInput: '有任务正在等你确认或补充信息，我会保留上下文，等你回复后继续。',
  bodySession: '可以继续当前对话；需要处理代码和文件时，再把项目接入这个工作空间。',
  bodyIdle: '可以从一个问题、任务或日常工作直接开始；需要编码时再接入项目。',
  continueTask: '继续处理当前任务',
  startChat: '开始对话',
  assistantReadyTitle: 'Stardust 助理',
  activeTaskTitle: '当前任务',
  currentConversationTitle: '当前对话',
  continueConversation: '继续当前对话',
  chooseProject: '选择项目',
  projectTools: '选择项目后启用编码工具',
  assistantMode: '助理模式',
  assistantActions: '助理快捷入口',
  assistantActionsBody: '不需要先打开代码项目。可以直接提问、继续当前对话，或者查看任务。',
  codingWorkspace: '开发工作台',
  codingWorkspaceBody: '需要编码时再选择项目，文件、Git 审查、终端上下文和实时预览会自动进入同一个工作空间。',
  gitDiffLabel: 'Git Diff',
  filesLabel: '文件',
  previewLabel: '预览',
  openTasks: '查看任务',
  agentWorking: 'Agent 执行中',
  agentNeedsInput: 'Agent 等待你的输入',
  agentReady: 'Agent 已就绪',
  environment: '工作环境',
  changes: '代码改动',
  phase: '当前阶段',
  context: '任务执行进度',
  contextIdle: '工作准备状态',
  activity: 'Agent 运行状态',
  activityIdle: '当前状态',
  gitChanges: '代码变更（Git Diff）',
  preview: '实时预览',
  branch: '当前分支',
  filesChanged: '个文件有改动',
  clean: '工作区干净',
  review: '审查变更',
  terminal: '打开终端',
  files: '打开文件',
  noProject: '未选择项目',
  noRepo: '非 Git 仓库',
  noPreview: '当前还没有打开预览',
  openPreview: '打开预览',
  noChanges: '当前没有本地改动',
  hiddenChanges: '仓库还有未显示的改动，请打开“审查”查看全部变更。',
  moreChanges: '项改动可在审查中查看',
  working: '执行中',
  needsInput: '等待你的输入',
  ready: '已就绪',
  connected: '后端连接正常',
  connecting: '正在连接后端',
  next: '下一步',
  pending: '待处理',
  completed: '已完成',
  cancelled: '已取消',
  inProgress: '进行中',
  failed: '失败',
  noActiveWork: '当前没有进行中的任务活动',
  command: '告诉我你想做什么，或者直接说话…',
  topCommand: '搜索命令、页面和功能…',
  moreActions: '更多操作'
}

function Panel({
  children,
  icon,
  title,
  className = ''
}: {
  children: ReactNode
  icon: string
  title: string
  className?: string
}) {
  return (
    <section
      className={
        'rounded-xl border border-[rgba(93,180,255,0.22)] bg-[rgba(5,18,34,0.76)] shadow-[inset_0_1px_0_rgba(255,255,255,0.035)] ' +
        className
      }
    >
      <header className="flex h-10 items-center gap-2 border-b border-[rgba(105,184,255,0.13)] px-3">
        <Codicon className="text-[#63c9ff]" name={icon} size="0.84rem" />
        <h2 className="text-[0.72rem] font-semibold tracking-[0.02em] text-(--ui-text-primary)">{title}</h2>
      </header>
      {children}
    </section>
  )
}

function statusIcon(item: ComposerStatusItem): string {
  return item.type === 'todo'
    ? 'checklist'
    : item.type === 'subagent'
      ? 'agent'
      : item.type === 'background'
        ? 'server-process'
        : 'target'
}

function statusDetail(item: ComposerStatusItem): string | undefined {
  if (item.currentTool) {
    return item.currentTool
  }

  if (!item.output) {
    return undefined
  }

  return item.output
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .at(-1)
}

export function WorkspaceView() {
  const { locale } = useI18n()
  const navigate = useNavigate()
  const copy = locale === 'zh' || locale === 'zh-hant' ? ZH : EN
  const cwd = useStore($currentCwd)
  const gatewayState = useStore($gatewayState)
  const previewTarget = useStore($previewTarget)
  const projectScope = useStore($projectScope)
  const projectTree = useStore($projectTree)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const sessions = useStore($sessions)
  const activeSessionId = useStore($activeSessionId)
  const attentionSessionIds = useStore($attentionSessionIds)
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
  const fallbackTaskRuntimeId = useStoreSelector($sessionStates, states =>
    fallbackTaskSession
      ? findLiveTaskRuntimeId(states, fallbackTaskSession)
      : fallbackTaskStoredId
        ? findLiveTaskRuntimeIdByStoredId(states, fallbackTaskStoredId)
        : null
  )
  const statusSessionId = selectedStoredSessionId ? activeSessionId : (fallbackTaskRuntimeId ?? activeSessionId)
  const statusItems = useSessionSlice($statusItemsBySession, statusSessionId)

  useEffect(() => registerRepoStatusCwd(effectiveCwd), [effectiveCwd])
  useEffect(() => {
    setSidebarOpen(true)
    setRightContextOpen(true)
    revealTreePane('workspace-overview')
  }, [])

  const effectiveRepoStatus = repoStatus
  const effectivePreviewTarget = previewTarget
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
  const sessionLabel = session
    ? storedSessionTitle(session)
    : selectedStoredSessionId
      ? primaryAttention || primaryWorking
        ? copy.activeTaskTitle
        : copy.currentConversationTitle
      : fallbackTaskSession
        ? storedSessionTitle(fallbackTaskSession)
        : fallbackTaskStoredId
          ? copy.activeTaskTitle
          : copy.assistantReadyTitle
  const activeTaskTarget =
    primaryAttention || primaryWorking
      ? selectedStoredSessionId
        ? sessionRoute(selectedStoredSessionId)
        : fallbackTaskStoredId
          ? sessionRoute(fallbackTaskStoredId)
          : null
      : null
  const normalizedCwd = effectiveCwd.replace(/[/\\]+$/, '')
  const projectName = normalizedCwd.split(/[/\\]/).filter(Boolean).at(-1) ?? copy.noProject
  const changedFiles = effectiveRepoStatus?.files ?? []
  const changedCount = effectiveRepoStatus?.changed ?? changedFiles.length
  const previewedChangeCount = Math.min(changedFiles.length, 4)
  const remainingChangeCount = Math.max(0, changedCount - previewedChangeCount)
  const branch = effectiveRepoStatus?.branch || copy.noRepo
  const gatewayReady = gatewayState === 'open'
  const todoItems = statusItems.filter(item => item.type === 'todo')
  const activityStatusItems = [
    ...statusItems.filter(item => item.state === 'failed'),
    ...statusItems.filter(item => item.state === 'running')
  ]
  const attentionSessions = sessions.filter(candidate =>
    attentionSessionIds.some(storedId => sessionMatchesStoredId(candidate, storedId))
  )
  const runningSessions = sessions.filter(candidate =>
    workingSessionIds.some(storedId => sessionMatchesStoredId(candidate, storedId))
  )
  const heroBody = anyAttention
    ? copy.bodyNeedsInput
    : anyWorking
      ? copy.bodyWorking
      : selectedStoredSessionId
        ? copy.bodySession
        : copy.bodyIdle
  const progressRows =
    todoItems.length > 0
      ? todoItems.map(item => ({
          label: item.title,
          meta:
            item.todoStatus === 'completed'
              ? copy.completed
              : item.todoStatus === 'in_progress'
                ? copy.inProgress
                : item.todoStatus === 'cancelled'
                  ? copy.cancelled
                  : copy.pending,
          state:
            item.todoStatus === 'completed'
              ? ('done' as const)
              : item.todoStatus === 'in_progress'
                ? ('active' as const)
                : ('pending' as const)
        }))
      : [
          ...(effectiveCwd
            ? [
                {
                  label: projectName,
                  meta: copy.ready,
                  state: 'done' as const
                },
                {
                  label: effectiveRepoStatus ? branch : copy.noRepo,
                  meta: copy.ready,
                  state: effectiveRepoStatus ? ('done' as const) : ('pending' as const)
                }
              ]
            : [
                {
                  label: copy.assistantMode,
                  meta: copy.ready,
                  state: 'done' as const
                }
              ]),
          {
            label: gatewayReady ? copy.connected : copy.connecting,
            meta: gatewayReady ? copy.ready : copy.next,
            state: gatewayReady ? ('done' as const) : ('active' as const)
          },
          {
            label: anyAttention ? copy.agentNeedsInput : anyWorking ? copy.agentWorking : copy.agentReady,
            meta: anyAttention ? copy.needsInput : anyWorking ? copy.working : copy.ready,
            state: anyAttention ? ('attention' as const) : anyWorking ? ('active' as const) : ('done' as const)
          }
        ]

  return (
    <main
      className="jarvis-workspace relative h-full min-h-0 overflow-x-hidden overflow-y-auto bg-[#050d18] text-(--ui-text-secondary)"
      data-jarvis-workspace=""
    >
      <img
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 z-0 h-full w-full select-none object-cover opacity-[0.08]"
        src={assetPath('ds-assets/filler-bg0.jpg')}
      />
      <img
        alt=""
        aria-hidden="true"
        className="jarvis-earth pointer-events-none absolute right-[-9rem] top-[-8rem] z-0 w-[42rem] max-w-[58vw] select-none opacity-55"
        src={assetPath('ds-assets/nasa-earth-at-night-asia.jpg')}
      />
      <div className="relative z-10 mx-auto flex min-h-full w-full max-w-[1360px] flex-col px-6 pb-6 pt-[calc(var(--titlebar-height)+0.9rem)]">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-[1.15rem] font-semibold tracking-[-0.02em] text-[#e7f5ff]">{copy.eyebrow}</h1>
            <p className="mt-1 max-w-3xl truncate text-[0.7rem] text-[#7895aa]">{heroBody}</p>
          </div>
          <button
            aria-label={copy.topCommand}
            className="flex shrink-0 items-center gap-2 rounded-lg border border-[rgba(83,190,255,0.16)] bg-[rgba(7,22,38,0.66)] px-3 py-1.5 text-[0.6rem] text-[#7597af] hover:border-[rgba(105,203,255,0.34)] hover:text-[#acdfff]"
            onClick={openCommandPalette}
            type="button"
          >
            <Codicon name="search" size="0.7rem" />
            <span>{locale === 'zh' || locale === 'zh-hant' ? '命令' : 'Commands'}</span>
            <span className="rounded border border-[rgba(102,173,222,0.12)] px-1.5 py-0.5 text-[0.52rem] text-[#65869d]">⌘ K</span>
          </button>
        </header>

        <section className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-[rgba(91,188,255,0.24)] bg-[rgba(8,27,48,0.76)] px-3.5 py-2.5 shadow-[0_0_24px_rgba(27,131,215,0.06)]">
          <span className="flex size-9 items-center justify-center rounded-lg border border-[rgba(90,190,255,0.22)] bg-[rgba(37,126,205,0.12)] text-[#62c7ff]">
            <Codicon name={effectiveCwd ? 'code' : 'comment'} size="1rem" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-[#edf8ff]">{sessionLabel}</div>
            <div className="mt-1 truncate text-[0.68rem] text-[#84a1b6]">
              {effectiveCwd ? projectName : copy.assistantMode}
              {effectiveRepoStatus?.branch ? ` · ${effectiveRepoStatus.branch}` : ''}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {activeTaskTarget && (
              <Button onClick={() => navigate(activeTaskTarget)} size="sm" variant="outline">
                {copy.continueTask}
              </Button>
            )}
            {!effectiveCwd && (
              <Button onClick={() => void openFolderAsProject()} size="sm" variant="outline">
                {copy.chooseProject}
              </Button>
            )}
            <span
              className={
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.65rem] font-medium ' +
                (primaryAttention
                  ? 'border-[rgba(251,191,36,0.22)] bg-[rgba(180,120,24,0.12)] text-amber-300'
                  : primaryWorking
                    ? 'border-[rgba(98,201,255,0.22)] bg-[rgba(44,151,230,0.12)] text-[#72cdfd]'
                    : 'border-[rgba(81,225,184,0.18)] bg-[rgba(26,136,110,0.12)] text-emerald-300')
              }
            >
              <Codicon name={primaryAttention ? 'warning' : 'circle-filled'} size="0.62rem" />
              {primaryAttention ? copy.needsInput : primaryWorking ? copy.working : copy.ready}
            </span>
          </div>
        </section>

        <div className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-[rgba(105,184,255,0.1)] px-1 pb-2.5 text-[0.62rem] text-[#6e8ca1]">
          <span className="inline-flex items-center gap-1.5">
            <Codicon
              className={anyAttention ? 'text-amber-300' : anyWorking ? 'text-[#65c9ff]' : 'text-emerald-300'}
              name={anyAttention ? 'warning' : anyWorking ? 'loading' : 'pass-filled'}
              size="0.66rem"
            />
            <span>{anyAttention ? copy.needsInput : anyWorking ? copy.working : copy.agentReady}</span>
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Codicon className={gatewayReady ? 'text-emerald-300' : 'text-amber-300'} name="server-environment" size="0.66rem" />
            <span>{gatewayReady ? copy.connected : copy.connecting}</span>
          </span>
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <Codicon className="shrink-0 text-[#65c9ff]" name={effectiveCwd ? 'git-branch' : 'code'} size="0.66rem" />
            <span className="truncate">
              {effectiveCwd
                ? `${branch} · ${changedCount > 0 ? `${changedCount} ${copy.filesChanged}` : copy.clean}`
                : copy.projectTools}
            </span>
          </span>
        </div>

        <div className="mt-3 grid min-h-[340px] gap-3 lg:grid-cols-[minmax(0,1.08fr)_minmax(0,0.92fr)]">
          <Panel icon="checklist" title={todoItems.length > 0 ? copy.context : copy.contextIdle}>
            <div className="flex h-full flex-col p-3">
              {progressRows.map((row, index) => (
                <div className="relative flex min-h-9 gap-3" key={`${index}-${row.label}`}>
                  {index < progressRows.length - 1 && (
                    <span className="absolute left-[0.46rem] top-[1.2rem] h-[calc(100%-0.4rem)] w-px bg-[rgba(72,188,238,0.17)]" />
                  )}
                  <div className="relative z-10 flex w-4 items-start justify-center pt-1">
                    <Codicon
                      className={
                        row.state === 'done'
                          ? 'text-emerald-300'
                          : row.state === 'attention'
                            ? 'text-amber-300'
                            : row.state === 'active'
                              ? 'text-[#65c9ff]'
                              : 'text-[#557086]'
                      }
                      name={
                        row.state === 'done'
                          ? 'pass-filled'
                          : row.state === 'attention'
                            ? 'warning'
                            : row.state === 'active'
                              ? 'record'
                              : 'circle-large-outline'
                      }
                      size="0.82rem"
                    />
                  </div>
                  <div className="min-w-0 flex-1 pb-2">
                    <div className="flex items-center gap-3">
                      <div className="min-w-0 flex-1 truncate text-[0.69rem] font-medium text-[#dcecf7]">{row.label}</div>
                      {row.meta && (
                        <div
                          className={
                            row.state === 'attention'
                              ? 'shrink-0 text-[0.58rem] text-amber-300'
                              : row.state === 'active'
                                ? 'shrink-0 text-[0.58rem] text-[#62c7ff]'
                                : 'shrink-0 text-[0.58rem] text-[#5c788e]'
                          }
                        >
                          {row.meta}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}

              <div className="mt-auto flex flex-wrap gap-2 border-t border-[rgba(105,184,255,0.1)] pt-3">
                <Button onClick={() => revealDesktopPane('terminal')} size="sm" variant="outline">
                  <Codicon name="terminal" size="0.78rem" />
                  {copy.terminal}
                </Button>
                {effectiveCwd ? (
                  <Button onClick={() => revealDesktopPane('files')} size="sm" variant="outline">
                    <Codicon name="files" size="0.78rem" />
                    {copy.files}
                  </Button>
                ) : (
                  <Button onClick={() => void openFolderAsProject()} size="sm" variant="outline">
                    <Codicon name="folder-opened" size="0.78rem" />
                    {copy.chooseProject}
                  </Button>
                )}
              </div>
            </div>
          </Panel>

          <Panel
            icon="pulse"
            title={
              attentionSessions.length > 0 || anyAttention || activityStatusItems.length > 0 || runningSessions.length > 0
                ? copy.activity
                : copy.activityIdle
            }
          >
            <div className="flex h-full flex-col gap-2 p-3 font-mono text-[0.66rem] leading-5 text-[#85a7bf]">
              {attentionSessions.length > 0 ? (
                <div className="space-y-2">
                  {attentionSessions.slice(0, 5).map(attentionSession => {
                    const sessionWorkspace = (attentionSession.cwd || '')
                      .replace(/[/\\]+$/, '')
                      .split(/[/\\]/)
                      .filter(Boolean)
                      .at(-1)

                    return (
                      <button
                        className="flex w-full min-w-0 items-center gap-2 rounded-lg border border-[rgba(251,191,36,0.14)] bg-[rgba(54,35,8,0.32)] px-2.5 py-2 text-left hover:border-[rgba(251,191,36,0.25)] hover:bg-[rgba(64,42,10,0.42)]"
                        key={attentionSession.id}
                        onClick={() => navigate(sessionRoute(attentionSession.id))}
                        type="button"
                      >
                        <Codicon className="shrink-0 text-amber-300" name="warning" size="0.72rem" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[#d8e5ec]">{storedSessionTitle(attentionSession)}</span>
                          {(sessionWorkspace || attentionSession.model) && (
                            <span className="mt-0.5 block truncate text-[0.56rem] text-[#7d7465]">
                              {[sessionWorkspace, attentionSession.model].filter(Boolean).join(' · ')}
                            </span>
                          )}
                        </span>
                        <span className="shrink-0 text-[0.56rem] text-amber-300">{copy.needsInput}</span>
                      </button>
                    )
                  })}
                </div>
              ) : activityStatusItems.length > 0 ? (
                <div className="space-y-2">
                  {activityStatusItems.slice(0, 5).map(item => {
                    const failed = item.state === 'failed'
                    const detail = statusDetail(item) ?? (failed && item.exitCode !== undefined ? `exit ${item.exitCode}` : undefined)

                    return (
                      <div
                        className={
                          'rounded-lg border px-2.5 py-2 ' +
                          (failed
                            ? 'border-[rgba(248,113,113,0.16)] bg-[rgba(54,16,20,0.3)]'
                            : 'border-[rgba(94,178,235,0.11)] bg-[rgba(2,12,22,0.58)]')
                        }
                        key={item.id}
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <Codicon
                            className={failed ? 'shrink-0 text-red-300' : 'shrink-0 text-[#62c7ff]'}
                            name={failed ? 'error' : statusIcon(item)}
                            size="0.72rem"
                          />
                          <span className="min-w-0 flex-1 truncate text-[#c0d8e8]">{item.title}</span>
                          <span className={failed ? 'shrink-0 text-[0.56rem] text-red-300' : 'shrink-0 text-[0.56rem] text-[#63c9ff]'}>
                            {failed ? copy.failed : copy.inProgress}
                          </span>
                        </div>
                        {detail && (
                          <div className={failed ? 'mt-1 truncate pl-5 text-[0.58rem] text-red-200/70' : 'mt-1 truncate pl-5 text-[0.58rem] text-[#607f95]'}>
                            {detail}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              ) : runningSessions.length > 0 ? (
                <div className="space-y-2">
                  {runningSessions.slice(0, 5).map(runningSession => {
                    const sessionWorkspace = (runningSession.cwd || '')
                      .replace(/[/\\]+$/, '')
                      .split(/[/\\]/)
                      .filter(Boolean)
                      .at(-1)

                    return (
                      <button
                        className="flex w-full min-w-0 items-center gap-2 rounded-lg border border-[rgba(94,178,235,0.11)] bg-[rgba(2,12,22,0.58)] px-2.5 py-2 text-left hover:border-[rgba(94,178,235,0.2)] hover:bg-[rgba(8,25,40,0.7)]"
                        key={runningSession.id}
                        onClick={() => navigate(sessionRoute(runningSession.id))}
                        type="button"
                      >
                        <Codicon className="shrink-0 text-[#62c7ff]" name="loading" size="0.72rem" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[#c0d8e8]">{storedSessionTitle(runningSession)}</span>
                          {(sessionWorkspace || runningSession.model) && (
                            <span className="mt-0.5 block truncate text-[0.56rem] text-[#607f95]">
                              {[sessionWorkspace, runningSession.model].filter(Boolean).join(' · ')}
                            </span>
                          )}
                        </span>
                        <span className="shrink-0 text-[0.56rem] text-[#63c9ff]">{copy.working}</span>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <Codicon
                      className={gatewayReady ? 'text-emerald-300' : 'text-amber-300'}
                      name="circle-filled"
                      size="0.62rem"
                    />
                    <span>{gatewayReady ? copy.connected : copy.connecting}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Codicon className="text-[#55bfff]" name={effectiveCwd ? 'git-branch' : 'comment'} size="0.72rem" />
                    <span>
                      {effectiveCwd ? (
                        <>
                          {copy.branch}: <span className="text-[#b9ddf5]">{branch}</span>
                        </>
                      ) : (
                        copy.assistantMode
                      )}
                    </span>
                  </div>
                  {effectiveCwd && (
                    <div className="flex items-center gap-2">
                      <Codicon className="text-[#55bfff]" name="git-compare" size="0.72rem" />
                      <span>{changedCount > 0 ? `${changedCount} ${copy.filesChanged}` : copy.clean}</span>
                    </div>
                  )}
                  {effectivePreviewTarget && (
                    <div className="flex min-w-0 items-center gap-2">
                      <Codicon className="shrink-0 text-[#55bfff]" name="preview" size="0.72rem" />
                      <span className="truncate">{effectivePreviewTarget.label}</span>
                    </div>
                  )}
                  <div
                    className={
                      'mt-auto rounded-lg border bg-[#06111e] p-3 ' +
                      (anyAttention
                        ? 'border-[rgba(251,191,36,0.16)] text-amber-300'
                        : 'border-[rgba(105,184,255,0.12)] text-[#6f8799]')
                    }
                  >
                    {anyAttention ? copy.needsInput : anyWorking ? copy.agentWorking : copy.noActiveWork}
                  </div>
                </>
              )}
            </div>
          </Panel>

          {effectiveCwd && (
            <Panel icon="git-compare" title={copy.gitChanges}>
              <div className="p-2">
                {changedFiles.length > 0 ? (
                  <div className="flex flex-col">
                    {changedFiles.slice(0, previewedChangeCount).map(file => (
                      <button
                        className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-[rgba(67,154,219,0.08)]"
                        key={file.path}
                        onClick={() => void openReviewForPath(file.path)}
                        type="button"
                      >
                        <Codicon
                          className="shrink-0 text-[#6fc9ff]"
                          name={file.conflicted ? 'warning' : file.untracked ? 'diff-added' : 'diff-modified'}
                          size="0.78rem"
                        />
                        <span className="min-w-0 flex-1 truncate font-mono text-[0.65rem] text-[#a8c0d1]">{file.path}</span>
                        {file.staged && <span className="text-[0.58rem] text-emerald-300">staged</span>}
                      </button>
                    ))}
                    {remainingChangeCount > 0 && (
                      <div className="px-2 pt-1 text-[0.58rem] text-[#63849a]">
                        {remainingChangeCount} {copy.moreChanges}
                      </div>
                    )}
                    <Button className="mt-2" onClick={() => revealDesktopPane('review')} size="sm" variant="outline">
                      {copy.review}
                    </Button>
                  </div>
                ) : changedCount > 0 ? (
                  <div className="flex min-h-32 flex-col items-center justify-center gap-3 px-4 text-center text-[0.68rem] text-[#688397]">
                    <span>{copy.hiddenChanges}</span>
                    <Button onClick={() => revealDesktopPane('review')} size="sm" variant="outline">
                      {copy.review}
                    </Button>
                  </div>
                ) : (
                  <div className="flex min-h-32 items-center justify-center px-4 text-center text-[0.68rem] text-[#688397]">
                    {copy.noChanges}
                  </div>
                )}
              </div>
            </Panel>
          )}

          {effectivePreviewTarget || effectiveCwd ? (
            <Panel icon="preview" title={copy.preview}>
              <div className="flex min-h-44 flex-col p-2.5">
                {effectivePreviewTarget ? (
                  <>
                    <button
                      className="flex min-h-36 flex-1 flex-col rounded-md border border-[rgba(105,184,255,0.15)] bg-[#06111e] p-3 text-left hover:border-[rgba(105,184,255,0.28)]"
                      onClick={() => revealDesktopPane('preview')}
                      type="button"
                    >
                      <div className="flex w-full min-w-0 items-center gap-2 text-[0.58rem] text-[#6c8da4]">
                        <Codicon className="shrink-0 text-emerald-300" name="circle-filled" size="0.54rem" />
                        <span className="min-w-0 flex-1 truncate">{effectivePreviewTarget.label}</span>
                        <span className="shrink-0">{effectivePreviewTarget.kind}</span>
                      </div>
                      <div className="mt-4 min-w-0">
                        <div className="text-[0.62rem] text-[#63849a]">{copy.preview}</div>
                        <div className="mt-1 break-all font-mono text-[0.68rem] leading-5 text-[#b7d5e8]">
                          {effectivePreviewTarget.url}
                        </div>
                      </div>
                      <div className="mt-auto flex items-center gap-2 pt-4 text-[0.62rem] text-[#67caff]">
                        <Codicon name="open-preview" size="0.68rem" />
                        <span>{copy.openPreview}</span>
                      </div>
                    </button>
                  </>
                ) : (
                  <div className="flex flex-1 items-center justify-center text-center text-[0.68rem] text-[#688397]">
                    {copy.noPreview}
                  </div>
                )}
              </div>
            </Panel>
          ) : (
            <div className="lg:col-span-2">
              <Panel icon="code" title={copy.codingWorkspace}>
                <div className="flex min-h-36 items-center justify-between gap-5 p-4">
                  <div className="min-w-0">
                    <p className="max-w-2xl text-[0.72rem] leading-5 text-[#7897ad]">{copy.codingWorkspaceBody}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button onClick={() => void openFolderAsProject()} size="sm" variant="outline">
                        <Codicon name="folder-opened" size="0.78rem" />
                        {copy.chooseProject}
                      </Button>
                      <Button onClick={() => revealDesktopPane('terminal')} size="sm" variant="outline">
                        <Codicon name="terminal" size="0.78rem" />
                        {copy.terminal}
                      </Button>
                    </div>
                  </div>
                  <div className="hidden shrink-0 grid-cols-3 gap-2 text-center text-[0.58rem] text-[#5e7d93] md:grid">
                    <span className="rounded-md border border-[rgba(105,184,255,0.08)] bg-[rgba(4,15,27,0.5)] px-3 py-2">{copy.gitDiffLabel}</span>
                    <span className="rounded-md border border-[rgba(105,184,255,0.08)] bg-[rgba(4,15,27,0.5)] px-3 py-2">{copy.filesLabel}</span>
                    <span className="rounded-md border border-[rgba(105,184,255,0.08)] bg-[rgba(4,15,27,0.5)] px-3 py-2">{copy.previewLabel}</span>
                  </div>
                </div>
              </Panel>
            </div>
          )}
        </div>

      </div>
    </main>
  )
}
