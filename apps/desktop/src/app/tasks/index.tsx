import { useStore } from '@nanostores/react'
import { LOCAL_CONNECTION_ID } from '@stardust/shared'
import type { TaskRow as GatewayTaskRow, TasksListResult } from '@stardust/shared'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { PAGE_INSET_X } from '@/app/layout-constants'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { EmptyState } from '@/components/ui/empty-state'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n'
import { queryClient } from '@/lib/query-client'
import { $taskHistoryByScope, readTaskHistory } from '@/lib/task-history'
import {
  buildSessionTaskRows,
  groupTaskRows,
  stateMeta,
  summarizeTaskGroups,
  type TrackedTaskRow,
  trackTaskRows
} from '@/lib/task-model'
import { $clarifyRequests } from '@/store/clarify'
import { $backgroundStatusBySession } from '@/store/composer-status'
import { $activeConnectionId } from '@/store/connections'
import { $activeGatewayProfile } from '@/store/profile'
import {
  $approvalRequests,
  $secretRequests,
  $sudoRequests,
  $vaultCodeRequests,
  $vaultSaveLoginRequests,
  $vaultUnlockRequests
} from '@/store/prompts'
import { $connection, $sessions, sessionMatchesStoredId } from '@/store/session'
import { $sessionStates } from '@/store/session-states'
import { $subagentsBySession } from '@/store/subagents'

import { CronView } from '../cron'
import { sessionRoute } from '../routes'

const GROUP_LABEL: Record<string, Record<string, string>> = {
  en: {
    attention: 'Needs your attention',
    running: 'In progress',
    upcoming: 'Upcoming',
    recent: 'Recent',
    other: 'Other'
  },
  zh: { attention: '需要你处理', running: '进行中', upcoming: '即将开始', recent: '最近完成', other: '其他状态' },
  'zh-hant': {
    attention: '需要你處理',
    running: '進行中',
    upcoming: '即將開始',
    recent: '最近完了',
    other: '其他狀態'
  },
  ja: { attention: '要対応', running: '進行中', upcoming: '近日', recent: '最近完了', other: 'その他' },
  ru: { attention: 'Требует внимания', running: 'Выполняется', upcoming: 'Скоро', recent: 'Недавно', other: 'Другие' },
  ar: { attention: 'يتطلب انتباهك', running: 'قيد التنفيذ', upcoming: 'قريبًا', recent: 'حديثًا', other: 'أخرى' }
}

const TASK_STATE_LABEL: Record<string, Record<string, string>> = {
  en: { blocked: 'Blocked', cancelled: 'Cancelled', completed: 'Completed', failed: 'Failed', interrupted: 'Interrupted', paused: 'Paused', queued: 'Queued', running: 'Running', waiting_for_user: 'Waiting for you' },
  zh: { blocked: '受阻', cancelled: '已取消', completed: '已完成', failed: '失败', interrupted: '已中断', paused: '已暂停', queued: '排队中', running: '执行中', waiting_for_user: '等待你处理' },
  'zh-hant': { blocked: '受阻', cancelled: '已取消', completed: '已完成', failed: '失敗', interrupted: '已中斷', paused: '已暫停', queued: '排隊中', running: '執行中', waiting_for_user: '等待你處理' },
  ja: { blocked: 'ブロック中', cancelled: 'キャンセル済み', completed: '完了', failed: '失敗', interrupted: '中断', paused: '一時停止', queued: '待機中', running: '実行中', waiting_for_user: '対応待ち' },
  ru: { blocked: 'Заблокировано', cancelled: 'Отменено', completed: 'Завершено', failed: 'Ошибка', interrupted: 'Прервано', paused: 'Приостановлено', queued: 'В очереди', running: 'Выполняется', waiting_for_user: 'Ожидает вас' },
  ar: { blocked: 'محظور', cancelled: 'ملغى', completed: 'مكتمل', failed: 'فشل', interrupted: 'متوقف', paused: 'متوقف مؤقتًا', queued: 'في الانتظار', running: 'قيد التنفيذ', waiting_for_user: 'بانتظارك' }
}

const TASK_POLL_MS = 7000
const TASK_LIMIT = 50

const TASK_CENTER_LOAD_COPY: Record<string, { degraded: string; error: string; loading: string; retrying: string }> = {
  en: {
    degraded: 'Some saved task sources could not be read. Tasks from available sources are shown.',
    error: 'Could not load saved task status. Check the connection and try again.',
    loading: 'Loading saved task status…',
    retrying: 'Showing the last saved task status. The latest refresh failed.'
  },
  zh: {
    degraded: '部分已保存任务来源读取失败；下方显示的是可读取来源的任务。',
    error: '无法读取已保存的任务状态。请检查连接后重试。',
    loading: '正在读取已保存的任务状态…',
    retrying: '当前显示上次成功读取的任务状态；最近一次刷新失败。'
  },
  'zh-hant': {
    degraded: '部分已儲存任務來源讀取失敗；下方顯示可讀取來源的任務。',
    error: '無法讀取已儲存的任務狀態。請檢查連線後重試。',
    loading: '正在讀取已儲存的任務狀態…',
    retrying: '目前顯示上次成功讀取的任務狀態；最近一次更新失敗。'
  },
  ja: {
    degraded: '一部の保存済みタスクを読み込めませんでした。読み込み可能なタスクを表示しています。',
    error: '保存済みタスク状態を読み込めません。接続を確認して再試行してください。',
    loading: '保存済みタスク状態を読み込み中…',
    retrying: '前回取得したタスク状態を表示しています。最新の更新に失敗しました。'
  },
  ru: {
    degraded: 'Не удалось прочитать некоторые источники задач. Показаны доступные задачи.',
    error: 'Не удалось загрузить сохранённое состояние задач. Проверьте подключение и повторите попытку.',
    loading: 'Загрузка сохранённого состояния задач…',
    retrying: 'Показано последнее сохранённое состояние. Последнее обновление не удалось.'
  },
  ar: {
    degraded: 'تعذرت قراءة بعض مصادر المهام المحفوظة. تُعرض المهام المتاحة.',
    error: 'تعذر تحميل حالة المهام المحفوظة. تحقق من الاتصال ثم أعد المحاولة.',
    loading: 'جارٍ تحميل حالة المهام المحفوظة…',
    retrying: 'تُعرض آخر حالة محفوظة. فشل آخر تحديث.'
  }
}

interface TaskSnapshot {
  degraded: TasksListResult['degraded']
  rows: TrackedTaskRow[]
}

const timestamp = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value * 1000 : undefined

function durableTaskRow(task: GatewayTaskRow, profile: string, sessions: ReturnType<typeof $sessions.get>): TrackedTaskRow {
  const parentId = task.parent_session_id ?? undefined
  const session = parentId ? sessions.find(row => sessionMatchesStoredId(row, parentId)) : undefined
  const updatedAt = timestamp(task.updated_at)
  const startedAt = timestamp(task.started_at)
  const nextRunAt = timestamp(task.next_run_at)
  const observedAt = startedAt ?? updatedAt ?? Date.now()
  const current = task.progress_current
  const total = task.progress_total

  return {
    detail: task.detail || undefined,
    durability: task.durability,
    files: task.files,
    id: task.task_id,
    phase: task.phase ? { kind: 'text', text: task.phase } : { kind: 'none' },
    profile,
    progress: typeof current === 'number' && typeof total === 'number' ? { current, total } : null,
    rail: task.rail,
    recoverable: task.recoverable,
    recoveryAction: task.recovery_action ?? undefined,
    requiresApproval: task.requires_approval,
    sessionId: parentId,
    sessionTitle: session?.title ?? session?.preview ?? undefined,
    startedAt,
    state: task.state,
    storedSessionId: session?.id ?? parentId,
    summary: task.summary || undefined,
    title: task.title,
    updatedAt,
    finishedAt: task.terminal ? updatedAt ?? observedAt : undefined,
    observedAt,
    seq: 0
  }
}

function useTaskRows() {
  const { requestGateway } = useGatewayRequest()
  const approvals = useStore($approvalRequests)
  const background = useStore($backgroundStatusBySession)
  const clarifies = useStore($clarifyRequests)
  const secrets = useStore($secretRequests)
  const sessionStates = useStore($sessionStates)
  const subagents = useStore($subagentsBySession)
  const sudos = useStore($sudoRequests)
  const vaultCodes = useStore($vaultCodeRequests)
  const vaultSaves = useStore($vaultSaveLoginRequests)
  const vaultUnlocks = useStore($vaultUnlockRequests)
  const sessions = useStore($sessions)
  const connection = useStore($connection)
  const connectionId = useStore($activeConnectionId) ?? ''
  const historyConnectionId = connectionId || LOCAL_CONNECTION_ID
  const connectionKey = connectionId || connection?.baseUrl || LOCAL_CONNECTION_ID
  const profileId = useStore($activeGatewayProfile)
  const history = useStore($taskHistoryByScope)
  const ownerKey = JSON.stringify([connectionKey, profileId])
  const [now, setNow] = useState(() => Date.now())
  const [tracked, setTracked] = useState<{ ownerKey: string; rows: TrackedTaskRow[] }>({ ownerKey, rows: [] })

  useEffect(() => {
    const timer = globalThis.setInterval(() => setNow(Date.now()), 1000)

    return () => globalThis.clearInterval(timer)
  }, [])

  const durableQuery = useQuery({
    queryKey: ['task-center', 'durable', connectionKey, profileId],
    refetchInterval: TASK_POLL_MS,
    queryFn: async ({ queryKey, signal }): Promise<TaskSnapshot> => {
      const requestedProfile = profileId === 'default' ? '' : profileId
      const result = await requestGateway<TasksListResult>(
        'tasks.list',
        { profile: requestedProfile, limit: TASK_LIMIT },
        undefined,
        signal
      )
      if ((result.scoped ?? '') !== requestedProfile) {
        throw new Error('Task list profile scope mismatch')
      }
      const rows = result.tasks.map(task => durableTaskRow(task, profileId, $sessions.get()))
      const degraded = new Set(result.degraded)
      const previous = queryClient.getQueryData<TaskSnapshot>(queryKey)
      if (previous && degraded.size > 0) {
        const currentIds = new Set(rows.map(row => row.id))
        rows.push(...previous.rows.filter(row => degraded.has(row.rail as TasksListResult['degraded'][number]) && !currentIds.has(row.id)))
      }
      return { degraded: [...degraded], rows }
    }
  })

  const rows = useMemo(() => {
    const projected = buildSessionTaskRows(
      { approvals, background, clarifies, secrets, subagents, sudos, vaultCodes, vaultSaves, vaultUnlocks },
      runtimeId => {
        const storedId = runtimeId ? sessionStates[runtimeId]?.storedSessionId : undefined
        const session = storedId
          ? sessions.find(row => sessionMatchesStoredId(row, storedId))
          : runtimeId
            ? sessions.find(row => sessionMatchesStoredId(row, runtimeId))
            : undefined

        return {
          sessionTitle: session?.title ?? session?.preview ?? undefined,
          storedSessionId: storedId ?? session?.id
        }
      }
    )

    return projected
  }, [
    approvals,
    background,
    clarifies,

    secrets,
    sessionStates,
    sessions,
    subagents,
    sudos,
    vaultCodes,
    vaultSaves,
    vaultUnlocks
  ])

  useEffect(() => {
    setTracked(previous => ({
      ownerKey,
      rows: trackTaskRows(previous.ownerKey === ownerKey ? previous.rows : [], rows, Date.now())
    }))
  }, [rows, ownerKey])

  const degraded = durableQuery.data?.degraded ?? []
  const durableRows = useMemo(
    () =>
      (durableQuery.data?.rows ?? []).map(row => {
        const storedId = row.sessionId
        const session = storedId ? sessions.find(item => sessionMatchesStoredId(item, storedId)) : undefined
        return session ? { ...row, sessionTitle: session.title ?? session.preview ?? undefined, storedSessionId: session.id } : row
      }),
    [durableQuery.data?.rows, sessions]
  )
  const groups = useMemo(() => {
    const live = tracked.ownerKey === ownerKey ? tracked.rows : []
    const liveIds = new Set(live.map(row => row.id))
    const durableIds = new Set(durableRows.map(row => row.id))
    const prior = readTaskHistory({ connectionId: historyConnectionId, profileId, sessionId: '*' }, now, history).filter(
      row => !liveIds.has(row.id) && !durableIds.has(row.id)
    )
    return groupTaskRows([...durableRows.filter(row => !liveIds.has(row.id)), ...live, ...prior], now)
  }, [now, tracked, durableRows, ownerKey, historyConnectionId, profileId, history])

  return {
    degraded,
    groups,
    hasSnapshot: durableQuery.data !== undefined,
    isError: durableQuery.isError,
    isPending: durableQuery.isPending
  }
}

function TaskRow({ locale, row, onOpen }: { locale: string; row: TrackedTaskRow; onOpen: () => void }) {
  const railTitles: Partial<Record<string, string>> = {
    approval: 'Approval',
    clarify: 'Question',
    sudo: 'Administrator permission',
    secret: 'Secret required',
    'vault-unlock': 'Unlock password manager',
    'vault-save': 'Save login',
    'vault-code': 'Verification code',
    process: 'Background command',
    delegation: 'Delegated work'
  }
  const title = row.title || railTitles[row.rail] || row.rail
  const stateLabel = TASK_STATE_LABEL[locale]?.[row.state] ?? TASK_STATE_LABEL.en[row.state] ?? row.state
  const meta = stateMeta(row.state)
  const phase =
    row.phase.kind === 'text'
      ? row.phase.text
      : row.phase.kind === 'working-on'
        ? `Working on ${row.phase.tool}`
        : row.phase.kind === 'exit-code'
          ? `Exit code ${row.phase.code}`
          : ''
  const icon = !meta.known
    ? 'question'
    : meta.terminal
      ? meta.tone === 'bad'
        ? 'error'
        : 'pass'
      : meta.family === 'attention'
        ? 'warning'
        : meta.family === 'upcoming'
          ? 'watch'
          : 'loading'

  return (
    <li className="border-b border-(--ui-stroke-tertiary) last:border-0">
      <Button className="h-auto w-full justify-start rounded-none px-3 py-3 text-left" onClick={onOpen} variant="ghost">
        <Codicon
          className={
            meta.tone === 'bad' ? 'text-destructive' : meta.tone === 'active' ? 'text-primary' : 'text-muted-foreground'
          }
          name={icon}
          spinning={!meta.terminal && meta.family === 'active'}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{title}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {row.sessionTitle ?? row.sessionId ?? 'Session unavailable'}
            {` · ${stateLabel}`}
            {phase ? ` · ${phase}` : ''}
            {row.recoverable
              ? ` · ${locale === 'zh' ? '可恢复' : locale === 'zh-hant' ? '可恢復' : 'Recovery available'} (${row.recoveryAction ?? 'resume'})`
              : row.requiresApproval
                ? ` · ${locale === 'zh' ? '需要批准' : locale === 'zh-hant' ? '需要核准' : 'Approval required'}`
                : ''}
          </span>
          {(row.summary || row.detail) && (
            <span className="block truncate text-xs text-muted-foreground">{row.summary ?? row.detail}</span>
          )}
        </span>
        <Codicon name="chevron-right" />
      </Button>
    </li>
  )
}

export function TaskCenterView({ onOpenSession }: { onOpenSession?: (sessionId: string) => void }) {
  const { locale } = useI18n()
  const navigate = useNavigate()
  const [tab, setTab] = useState<'activity' | 'automation'>('activity')
  const taskState = useTaskRows()
  const { degraded, groups } = taskState
  const summary = useMemo(() => summarizeTaskGroups(groups), [groups])
  const loadCopy = TASK_CENTER_LOAD_COPY[locale] ?? TASK_CENTER_LOAD_COPY.en
  const labels = GROUP_LABEL[locale] ?? GROUP_LABEL.en
  const zh = locale === 'zh'
  const zhHant = locale === 'zh-hant'
  const title = zh ? '任务中心' : zhHant ? '任務中心' : 'Tasks'
  const activity = zh ? '活动' : zhHant ? '活動' : 'Activity'
  const automation = zh ? '自动化' : zhHant ? '自動化' : 'Automation'

  return (
    <section
      className="flex h-full min-w-0 flex-col overflow-hidden bg-(--ui-chat-surface-background)"
      data-task-center=""
    >
      <header
        className={`flex shrink-0 flex-wrap items-center justify-between gap-3 pb-3 pt-[calc(var(--titlebar-height)+0.5rem)] ${PAGE_INSET_X}`}
      >
        <div>
          <h1 className="text-lg font-semibold text-foreground">{title}</h1>
          <p className="text-xs text-muted-foreground">
            {zh
              ? `${summary.running} 项进行中 · ${summary.attention} 项需要处理`
              : `${summary.running} running · ${summary.attention} need attention`}
          </p>
        </div>
        <SegmentedControl
          aria-label={title}
          onChange={setTab}
          options={[
            { id: 'activity', label: activity },
            { id: 'automation', label: automation }
          ]}
          value={tab}
        />
      </header>
      <div className={`min-h-0 flex-1 overflow-y-auto pb-4 ${PAGE_INSET_X}`}>
        {tab === 'automation' ? (
          <div data-task-automation="">
            <CronView className="!pt-0" onOpenSession={onOpenSession} />
          </div>
        ) : groups.length || degraded.length || taskState.isError ? (
          <div className="space-y-3">
            {(degraded.length > 0 || taskState.isError) && (
              <p
                aria-live="polite"
                className="rounded-md border border-(--ui-stroke-tertiary) px-3 py-2 text-xs text-muted-foreground"
                role="status"
              >
                {degraded.length > 0 ? loadCopy.degraded : loadCopy.retrying}
              </p>
            )}
            {groups.length ? (
              <div className="space-y-5">
                {groups.map(group => (
                  <section key={group.id}>
                    <h2 className="mb-2 text-xs font-semibold text-muted-foreground">{labels[group.id]}</h2>
                    <ul className="rounded-lg border border-(--ui-stroke-tertiary)" data-task-group={group.id}>
                      {group.rows.map(row => (
                        <TaskRow
                          key={row.id}
                          locale={locale}
                          onOpen={() => {
                            if (row.storedSessionId) {
                              if (onOpenSession) {
                                onOpenSession(row.storedSessionId)
                              } else {
                                navigate(sessionRoute(row.storedSessionId))
                              }
                            }
                          }}
                          row={row}
                        />
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            ) : (
              <EmptyState
                description={taskState.isError ? loadCopy.error : loadCopy.degraded}
                title={zh ? (taskState.isError ? '任务读取失败' : '部分任务状态不可用') : (taskState.isError ? 'Could not load tasks' : 'Some tasks are unavailable')}
              />
            )}
          </div>
        ) : taskState.isPending || !taskState.hasSnapshot ? (
          <EmptyState description={loadCopy.loading} title={zh ? '正在加载任务' : 'Loading tasks'} />
        ) : taskState.isError ? (
          <EmptyState description={loadCopy.error} title={zh ? '任务读取失败' : 'Could not load tasks'} />
        ) : (
          <EmptyState
            description={
              zh
                ? '会话中的确认、后台命令和子代理进度会显示在这里。'
                : 'Approvals, background commands, and subagent progress from your sessions appear here.'
            }
            title={zh ? '暂时没有活动任务' : 'No active tasks'}
          />
        )}
      </div>
    </section>
  )
}
