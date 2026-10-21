import type { DelegationRecoveryReceipt } from '@stardust/shared'

import { StatusSection } from '@/components/chat/status-section'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'

interface RecoverySectionProps {
  receipts: readonly DelegationRecoveryReceipt[]
}

/**
 * Read-only notice for background delegations whose owner process exited before reporting back.
 *
 * "Unknown" is not "failed": the work may or may not have run, so this offers no retry and nothing here
 * submits, steers or re-dispatches anything. A batch shows its first goal and how many goals it held.
 */
export function RecoverySection({ receipts }: RecoverySectionProps) {
  const { t } = useI18n()
  const s = t.statusStack

  if (receipts.length === 0) {
    return null
  }

  return (
    <div className="composer-no-drag min-w-0" data-slot="composer-delegation-recovery">
      <StatusSection
        icon={<Codicon className="text-(--ui-yellow)" name="question" size="0.8rem" />}
        label={s.recoveryUnknown(receipts.length)}
      >
        <div className="max-h-[25vh] overflow-y-auto overscroll-contain">
          <p className="px-2 py-1 text-[0.68rem] text-(--ui-text-tertiary)">{s.recoveryNote}</p>
          {receipts.map(receipt => {
            const goals = receipt.goal_count ?? 1
            const batch = receipt.is_batch || goals > 1

            return (
              <div className="min-w-0 px-2 py-1" data-slot="composer-recovery-receipt" key={receipt.delegation_id}>
                {receipt.goal && <span className="block truncate text-xs text-(--ui-text-primary)">{receipt.goal}</span>}
                <span className="block truncate text-[0.68rem] text-(--ui-text-tertiary)">
                  {batch ? `${s.recoveryRowState} · ${s.recoveryGoalCount(goals)}` : s.recoveryRowState}
                </span>
              </div>
            )
          })}
        </div>
      </StatusSection>
    </div>
  )
}
