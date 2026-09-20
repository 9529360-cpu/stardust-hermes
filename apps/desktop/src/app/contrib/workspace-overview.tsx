import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect } from 'react'
import { useNavigate } from 'react-router'

import { $activePresetId } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { registry } from '@/contrib/registry'
import { useI18n } from '@/i18n'
import { sessionTitle as storedSessionTitle } from '@/lib/chat-runtime'
import { readKey, writeKey } from '@/lib/storage'
import { useSessionSlice, useStoreSelector } from '@/lib/use-session-slice'
import { registerRepoStatusCwd, repoStatusForCwd } from '@/store/coding-status'
import { $statusItemsBySession } from '@/store/composer-status'
import { applyDesktopLayoutPreset } from '@/store/pane-focus'
import { $projectScope, $projectTree, ALL_PROJECTS, projectRootCwd } from '@/store/projects'
import { setRightContextOpen } from '@/store/right-context'
import { $activeSessionId, $currentCwd, $selectedStoredSessionId, $sessions, sessionMatchesStoredId } from '@/store/session'
import { $attentionSessionIds, $sessionStates, $workingSessionIds } from '@/store/session-states'
import { isAuxiliaryWindow } from '@/store/windows'

import { sessionRoute } from '../routes'
import {
  findLiveTaskRuntimeId,
  findLiveTaskRuntimeIdByStoredId,
  findLiveTaskSession,
  findLiveTaskStoredId,
  resolveTaskWorkspaceCwd
} from '../workspace/task-session'

import { WORKSPACE_OVERVIEW_COPY } from './workspace-overview-copy'

export const WORKSPACE_OVERVIEW_PANE_ID = 'workspace-overview'

const PERSONAL_LAYOUT_VERSION = 3
const PERSONAL_LAYOUT_VERSION_KEY = 'hermes.desktop.personalLayoutVersion'

function Card({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="border-t border-(--ui-stroke-quaternary) py-3 first:border-t-0 first:pt-0">
      <div className="mb-2 text-[0.62rem] font-medium text-(--ui-text-tertiary)">{title}</div>
      {children}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 py-0.5 text-[0.68rem]">
      <span className="text-(--ui-text-tertiary)">{label}</span>
      <span className="min-w-0 truncate text-right font-medium text-(--ui-text-secondary)">{value}</span>
    </div>
  )
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

  return (
    <aside
      aria-label={copy.workspace}
      className="jarvis-context-rail flex h-full min-h-0 flex-col overflow-y-auto bg-(--ui-sidebar-surface-background) px-3 pb-3 pt-[calc(var(--titlebar-height)+0.75rem)] text-(--ui-text-secondary)"
      data-personal-overview=""
    >
      <div className="mb-2 flex items-center justify-between px-1">
        <div className="text-[0.68rem] font-semibold text-(--ui-text-primary)">{copy.workspace}</div>
        <Button aria-label={systemLabels.hidePreview} onClick={() => setRightContextOpen(false)} size="icon-xs" variant="ghost">
          <Codicon name="close" />
        </Button>
      </div>

      <div className="flex flex-col gap-2.5">
        <Card title={effectiveCwd ? copy.projectContext : systemLabels.assistantContext}>
          {effectiveCwd ? (
            <>
              <div className="flex items-start gap-2">
                <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name="folder" size="0.8rem" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[0.76rem] font-semibold text-(--ui-text-primary)">{projectName}</div>
                  <div className="mt-1 break-all font-mono text-[0.58rem] leading-4 text-(--ui-text-quaternary)">
                    {effectiveCwd}
                  </div>
                </div>
              </div>
              <div className="mt-3 border-t border-(--ui-stroke-quaternary) pt-2">
                <Metric label={copy.branch} value={<span className="font-mono">{branch}</span>} />
                {effectiveRepoStatus && (
                  <Metric label={copy.sync} value={copy.syncValue(effectiveRepoStatus.ahead, effectiveRepoStatus.behind)} />
                )}
              </div>
            </>
          ) : (
            <div className="flex items-start gap-2">
              <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name="comment" size="0.8rem" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[0.76rem] font-semibold text-(--ui-text-primary)">{sessionLabel}</div>
                <div className="mt-1 text-[0.6rem] leading-4 text-(--ui-text-quaternary)">{assistantContextSummary}</div>
              </div>
            </div>
          )}
        </Card>

        {showTaskCard && (
          <Card title={copy.currentResult}>
          <>
              <div className="flex items-start gap-2.5">
                <Codicon
                  className={
                    primaryWorking
                      ? 'mt-0.5 shrink-0 text-(--theme-primary)'
                      : 'mt-0.5 shrink-0 text-(--ui-text-tertiary)'
                  }
                  name={primaryAttention ? 'warning' : primaryWorking ? 'loading' : 'target'}
                  size="0.82rem"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[0.76rem] font-semibold text-(--ui-text-primary)">{sessionLabel}</div>
                  <div className="mt-1 text-[0.64rem] leading-5 text-(--ui-text-tertiary)">{summary}</div>
                  {displaySession?.model && (
                    <div className="mt-1 truncate font-mono text-[0.56rem] text-(--ui-text-quaternary)">{displaySession.model}</div>
                  )}
                  {displayTaskStoredId && (primaryAttention || primaryWorking) && (
                    <Button
                      className="mt-2 w-full justify-center"
                      onClick={() => navigate(sessionRoute(displayTaskStoredId))}
                      size="sm"
                      variant="outline"
                    >
                      <Codicon name="comment-discussion" size="0.72rem" />
                      {copy.continueTask}
                    </Button>
                  )}
                </div>
              </div>
              {todoItems.length > 0 && (
                <div className="mt-3 border-t border-(--ui-stroke-quaternary) pt-2.5">
                  <div className="flex items-center justify-between gap-2 text-[0.6rem] text-(--ui-text-tertiary)">
                    <span>{systemLabels.taskProgress}</span>
                    <span className="font-mono text-(--ui-text-secondary)">{completedTodoCount}/{todoItems.length}</span>
                  </div>
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-(--ui-bg-quaternary)">
                    <div
                      className="h-full rounded-full bg-(--theme-primary) transition-[width] duration-200"
                      style={{ width: `${todoPercent}%` }}
                    />
                  </div>
                  {activeTodo && (
                    <div className="mt-2 flex items-start gap-2 text-[0.58rem] leading-4 text-(--ui-text-tertiary)">
                      <Codicon className="mt-0.5 shrink-0 text-(--theme-primary)" name="record" size="0.58rem" />
                      <div className="min-w-0">
                        <div className="text-[0.52rem] uppercase tracking-[0.08em] text-(--ui-text-quaternary)">
                          {systemLabels.currentStep}
                        </div>
                        <div className="mt-0.5 line-clamp-2 text-(--ui-text-secondary)">{activeTodo.title}</div>
                      </div>
                    </div>
                  )}
                </div>
              )}
          </>
        </Card>
        )}
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
      revealAliases: ['overview', 'workspace-overview'],
      width: '420px',
      minWidth: '320px',
      maxWidth: '520px'
    },
    render: () => <WorkspaceOverview />
  })
}
