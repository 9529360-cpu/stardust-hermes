import type { ApprovalGrant } from '@hermes/shared'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { Clock, KeyRound } from '@/lib/icons'

import { ListRow, ListRowSkeleton, SettingsSection } from '../primitives'

import { ActivityEmpty } from './activity-empty'
import { useApprovalActivity } from './use-approval-activity'

/** Standing grants (revocable) and recent approval decisions. Read-mostly: nothing here grants new authority. */
export function ApprovalActivity() {
  const { t } = useI18n()
  const c = t.settings.activity
  const { decisions, grants, status, revokeFailed, reload, revoke } = useApprovalActivity()
  const [pendingRevoke, setPendingRevoke] = useState<ApprovalGrant | null>(null)
  const hasRows = grants.length > 0 || decisions.length > 0

  if (status === 'error' && !hasRows) {
    return (
      <SettingsSection icon={Clock} title={c.decisionsTitle}>
        <ErrorState title={c.loadFailed}>
          <Button onClick={() => void reload()} variant="outline">
            {c.retry}
          </Button>
        </ErrorState>
      </SettingsSection>
    )
  }

  const loading = status === 'loading' && !hasRows

  return (
    <>
      <SettingsSection icon={KeyRound} title={c.grantsTitle}>
        {loading ? <ListRowSkeleton /> : null}
        {!loading && grants.length === 0 ? <ActivityEmpty>{c.noGrants}</ActivityEmpty> : null}
        {grants.map(grant => (
          <ListRow
            action={
              <Button onClick={() => setPendingRevoke(grant)} variant="outline">
                {c.revoke}
              </Button>
            }
            description={c.grantKind[grant.action_kind as keyof typeof c.grantKind] ?? grant.action_kind}
            key={grant.id}
            title={grant.target}
          />
        ))}
        {revokeFailed ? (
          <p className="px-1 text-sm text-destructive" role="alert">
            {c.revokeFailed}
          </p>
        ) : null}
      </SettingsSection>

      <SettingsSection icon={Clock} title={c.decisionsTitle}>
        {loading ? <ListRowSkeleton /> : null}
        {!loading && decisions.length === 0 ? <ActivityEmpty>{c.noDecisions}</ActivityEmpty> : null}
        {decisions.map((entry, index) => (
          <ListRow
            description={entry.command_preview || entry.description}
            hint={formatTimestamp(entry.ts)}
            key={`${entry.ts}-${index}`}
            title={`${entry.tool_name} · ${entry.outcome}`}
          />
        ))}
      </SettingsSection>

      <ConfirmDialog
        cancelLabel={t.common.cancel}
        confirmLabel={c.revokeConfirm}
        description={c.revokeDescription}
        destructive
        dismissOnConfirm
        onClose={() => setPendingRevoke(null)}
        onConfirm={() => (pendingRevoke ? revoke(pendingRevoke) : undefined)}
        open={pendingRevoke !== null}
        title={c.revokeTitle}
      />
    </>
  )
}

function formatTimestamp(value: string): string {
  const date = new Date(value)

  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
