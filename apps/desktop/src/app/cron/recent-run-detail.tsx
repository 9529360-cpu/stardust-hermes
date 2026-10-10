import { PanelAction, PanelDetail, PanelMeta, PanelPill } from '@/app/overlays/panel'
import type { Translations } from '@/i18n/types'
import { AlertTriangle } from '@/lib/icons'

import {
  type RecentRun,
  RUN_TONE,
  runDeliveryLabel,
  runErrorText,
  runStatus,
  runStatusLabel,
  runTimeLabel
} from './recent-runs'

interface RecentRunDetailProps {
  c: Translations['cron']
  /** The job this run belongs to, when it is still in the list. */
  job: null | undefined | { id: string }
  locale: string
  onOpenJob: () => void
  run: RecentRun
  title: string
}

/** One scheduled run, explained in plain words: when it ran, where its result went, and what went wrong. */
export function RecentRunDetail({ c, job, locale, onOpenJob, run, title }: RecentRunDetailProps) {
  const rc = c.recentRuns
  const status = runStatus(run)
  const delivery = runDeliveryLabel(run, rc)
  const error = runErrorText(run)
  const finished = status === 'running' ? null : runTimeLabel(run.updated_at, locale, true)

  return (
    <PanelDetail>
      <header className="space-y-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="text-[0.95rem] font-semibold tracking-tight text-foreground">{title}</h3>
          <PanelPill tone={RUN_TONE[status]}>{runStatusLabel(run, rc)}</PanelPill>
        </div>

        <PanelMeta
          rows={[
            { label: rc.startedLabel, value: runTimeLabel(run.started_at, locale, true) },
            ...(finished ? [{ label: rc.finishedLabel, value: finished }] : []),
            ...(delivery ? [{ label: rc.deliveryLabel, value: delivery }] : [])
          ]}
        />

        {error ? (
          <div className="space-y-1.5 rounded bg-destructive/10 p-2 text-[0.7rem] text-destructive">
            <div className="flex items-start gap-1.5">
              <AlertTriangle className="mt-px size-3 shrink-0" />
              <span className="min-w-0 break-words">{rc.errorTitle}</span>
            </div>
            <p className="break-words pl-4 text-foreground/80">{error}</p>
          </div>
        ) : null}

        {job ? (
          <div>
            <PanelAction icon="arrow-right" onClick={onOpenJob}>
              {rc.openJob}
            </PanelAction>
          </div>
        ) : (
          <p className="text-[0.7rem] text-muted-foreground">{rc.jobRemoved}</p>
        )}
      </header>
    </PanelDetail>
  )
}
