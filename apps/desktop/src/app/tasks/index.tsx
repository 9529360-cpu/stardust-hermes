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

const TASK_POLL_MS = 7000
const TASK_LIMIT = 50

// Protocol phases belong to durable rows; free-form session text stays verbatim.
interface TaskCenterRow extends TrackedTaskRow {
  statusPhase?: string
}

interface TaskSnapshot {
  degraded: TasksListResult['degraded']
  rows: TaskCenterRow[]
}

const timestamp = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value * 1000 : undefined

function durableTaskRow(
  task: GatewayTaskRow,
  profile: string,
  sessions: ReturnType<typeof $sessions.get>
): TaskCenterRow {
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
    phase: { kind: 'none' },
    statusPhase: task.phase ?? undefined,
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
    finishedAt: task.terminal ? (updatedAt ?? observedAt) : undefined,
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
        rows.push(
          ...previous.rows.filter(
            row => degraded.has(row.rail as TasksListResult['degraded'][number]) && !currentIds.has(row.id)
          )
        )
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

        return session
          ? { ...row, sessionTitle: session.title ?? session.preview ?? undefined, storedSessionId: session.id }
          : row
      }),
    [durableQuery.data?.rows, sessions]
  )

  const groups = useMemo(() => {
    const live = tracked.ownerKey === ownerKey ? tracked.rows : []
    const liveIds = new Set(live.map(row => row.id))
    const durableIds = new Set(durableRows.map(row => row.id))

    const prior = readTaskHistory(
      { connectionId: historyConnectionId, profileId, sessionId: '*' },
      now,
      history
    ).filter(row => !liveIds.has(row.id) && !durableIds.has(row.id))

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

const translatedToken = (labels: object, value: string): string | undefined =>
  Object.hasOwn(labels, value) ? (labels as Record<string, string>)[value] : undefined

export function TaskRow({ row, onOpen }: { row: TaskCenterRow; onOpen: () => void }) {
  const { t } = useI18n()
  const copy = t.taskCenter
  const title = row.title || translatedToken(copy.rails, row.rail) || copy.unknownRail(row.rail)
  const stateLabel = translatedToken(copy.states, row.state) ?? copy.unknownStatus(row.state)
  const meta = stateMeta(row.state)
  const statusPhase = row.statusPhase

  const phase = statusPhase
    ? (translatedToken(copy.states, statusPhase) ??
      translatedToken(copy.phases, statusPhase) ??
      (statusPhase === 'done'
        ? copy.states.completed
        : statusPhase === 'error'
          ? copy.states.failed
          : copy.unknownStatus(statusPhase)))
    : row.phase.kind === 'text'
      ? row.phase.text
      : row.phase.kind === 'working-on'
        ? copy.workingOn(row.phase.tool)
        : row.phase.kind === 'exit-code'
          ? copy.exitCode(row.phase.code)
          : row.phase.kind === 'needs-secret'
            ? copy.needsSecret(row.phase.name)
            : row.phase.kind === 'needs-vault'
              ? copy.needsVault(row.phase.name)
              : row.phase.kind === 'running'
                ? copy.states.running
                : ''

  const recoveryAction = row.recoveryAction ?? 'resume'
  const recoveryLabel = translatedToken(copy.recoveryActions, recoveryAction) ?? copy.unknownRecovery(recoveryAction)

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
            {row.sessionTitle ?? row.sessionId ?? copy.sessionUnavailable}
            {` · ${stateLabel}`}
            {phase ? ` · ${phase}` : ''}
            {row.recoverable
              ? ` · ${copy.recoveryAvailable} (${recoveryLabel})`
              : row.requiresApproval
                ? ` · ${copy.approvalRequired}`
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
  const { t } = useI18n()
  const navigate = useNavigate()
  const [tab, setTab] = useState<'activity' | 'automation'>('activity')
  const taskState = useTaskRows()
  const { degraded, groups } = taskState
  const copy = t.taskCenter
  const loadCopy = copy.load
  const labels = copy.groups
  const title = copy.title
  const activity = copy.activity
  const automation = copy.automation

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
                    <h2 className="mb-2 text-xs font-semibold text-muted-foreground">
                      {translatedToken(labels, group.id) ?? copy.groups.other}
                    </h2>
                    <ul className="rounded-lg border border-(--ui-stroke-tertiary)" data-task-group={group.id}>
                      {group.rows.map(row => (
                        <TaskRow
                          key={row.id}
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
                title={taskState.isError ? copy.loadFailed : copy.someUnavailable}
              />
            )}
          </div>
        ) : taskState.isPending || !taskState.hasSnapshot ? (
          <EmptyState description={loadCopy.loading} title={copy.loading} />
        ) : taskState.isError ? (
          <EmptyState description={loadCopy.error} title={copy.loadFailed} />
        ) : (
          <EmptyState title={copy.emptyTitle} />
        )}
      </div>
    </section>
  )
}
