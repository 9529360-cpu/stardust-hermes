import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import type { PullRequestChecksState } from '@/store/pull-requests'

const CHECK_STYLE: Record<PullRequestChecksState, { className: string; icon: string; spinning?: boolean }> = {
  failed: { className: 'text-(--ui-red)', icon: 'error' },
  loading: { className: 'text-(--theme-primary)', icon: 'loading', spinning: true },
  passed: { className: 'text-(--ui-green)', icon: 'check' },
  pending: { className: 'text-amber-500', icon: 'warning' },
  unavailable: { className: 'text-(--ui-text-quaternary)', icon: 'circle-slash' }
}

export function PrChecksBadge({
  className,
  compact = false,
  state
}: {
  className?: string
  compact?: boolean
  state: PullRequestChecksState
}) {
  const { t } = useI18n()
  const style = CHECK_STYLE[state]
  const label = t.statusStack.coding.checks[state]

  return (
    <span
      aria-label={label}
      className={cn(
        'inline-flex shrink-0 items-center gap-1 whitespace-nowrap leading-none',
        compact ? 'text-[0.625rem]' : 'text-[0.68rem]',
        style.className,
        className
      )}
      data-pr-checks-state={state}
      title={label}
    >
      <Codicon aria-hidden name={style.icon} size={compact ? '0.7rem' : '0.78rem'} spinning={style.spinning} />
      {compact ? <span className="sr-only">{label}</span> : <span>{label}</span>}
    </span>
  )
}
