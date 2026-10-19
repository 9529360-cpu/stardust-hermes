import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import { PAGE_INSET_X } from '@/app/layout-constants'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { EmptyState } from '@/components/ui/empty-state'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n'
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
import { $sessions, sessionMatchesStoredId } from '@/store/session'
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

function useTaskRows() {
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
  const connectionId = useStore($activeConnectionId) ?? ''
  const profileId = useStore($activeGatewayProfile)
  const history = useStore($taskHistoryByScope)
  const ownerKey = JSON.stringify([connectionId, profileId])
  const [now, setNow] = useState(() => Date.now())
  const [tracked, setTracked] = useState<{ ownerKey: string; rows: TrackedTaskRow[] }>({ ownerKey, rows: [] })

  useEffect(() => {
    const timer = globalThis.setInterval(() => setNow(Date.now()), 1000)

    return () => globalThis.clearInterval(timer)
  }, [])

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

  return useMemo(() => {
    const live = tracked.ownerKey === ownerKey ? tracked.rows : []
    const liveIds = new Set(live.map(row => row.id))
    const prior = readTaskHistory({ connectionId, profileId, sessionId: '*' }, now).filter(row => !liveIds.has(row.id))
    return groupTaskRows([...live, ...prior], now)
  }, [now, tracked, ownerKey, connectionId, profileId, history])
}

function TaskRow({ row, onOpen }: { row: TrackedTaskRow; onOpen: () => void }) {
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
            {phase ? ` · ${phase}` : ''}
          </span>
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
  const groups = useTaskRows()
  const summary = useMemo(() => summarizeTaskGroups(groups), [groups])
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
        ) : groups.length ? (
          <div className="space-y-5">
            {groups.map(group => (
              <section key={group.id}>
                <h2 className="mb-2 text-xs font-semibold text-muted-foreground">{labels[group.id]}</h2>
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
