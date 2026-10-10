import type { ApprovalGrant } from '@hermes/shared'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { Clock, ShieldLock } from '@/lib/icons'

import { ListRow, ListRowSkeleton, SettingsSection } from '../primitives'

import { ActivityEmpty } from './activity-empty'
import { type DecisionTone, describeGrant, groupDecisions } from './approval-presentation'
import { useApprovalActivity } from './use-approval-activity'

const TONE_CLASS: Record<DecisionTone, string> = {
  allowed: 'text-foreground',
  blocked: 'text-destructive',
  neutral: 'text-muted-foreground'
}

/**
 * What I may do without asking, and what I recently asked you about. Everything reads as a plain
 * sentence. The only write here is revoking a permission, and it always asks first.
 */
export function ApprovalActivity() {
  const { locale, t } = useI18n()
  const c = t.settings.activity
  const { decisions, grants, status, revokeFailed, reload, revoke } = useApprovalActivity()
  const [pendingRevoke, setPendingRevoke] = useState<ApprovalGrant | null>(null)
  const hasRows = grants.length > 0 || decisions.length > 0

  if (status === 'error' && !hasRows) {
    return (
      <SettingsSection icon={ShieldLock} title={c.permissionsTitle}>
        <ErrorState title={c.loadFailed}>
          <Button onClick={() => void reload()} variant="outline">
            {c.retry}
          </Button>
        </ErrorState>
      </SettingsSection>
    )
  }

  const loading = status === 'loading' && !hasRows
  const now = new Date()
  const groups = groupDecisions(decisions, c, locale, now)

  return (
    <>
      <SettingsSection icon={ShieldLock} title={c.permissionsTitle}>
        <p className="px-1 pb-1 text-sm text-muted-foreground">{c.permissionsIntro}</p>
        {loading ? <ListRowSkeleton /> : null}
        {!loading && grants.length === 0 ? <ActivityEmpty>{c.permissionsEmpty}</ActivityEmpty> : null}
        {grants.map(grant => {
          const view = describeGrant(grant, c, locale, now)

          return (
            <ListRow
              action={
                <Button onClick={() => setPendingRevoke(grant)} variant="outline">
                  {c.revoke}
                </Button>
              }
              description={view.detail}
              key={grant.id}
              title={view.title}
            />
          )
        })}
        {revokeFailed ? (
          <p className="px-1 text-sm text-destructive" role="alert">
            {c.revokeFailed}
          </p>
        ) : null}
      </SettingsSection>

      <SettingsSection icon={Clock} title={c.confirmationsTitle}>
        {loading ? <ListRowSkeleton /> : null}
        {!loading && groups.length === 0 ? <ActivityEmpty>{c.confirmationsEmpty}</ActivityEmpty> : null}
        {groups.map(group => (
          <div className="grid gap-0.5" key={group.key}>
            <p className="px-1 pt-2 text-xs font-medium text-muted-foreground">{group.label}</p>
            {group.rows.map(row => (
              <ListRow
                description={row.time}
                hint={<span className={TONE_CLASS[row.tone]}>{row.outcome}</span>}
                key={row.id}
                title={row.title}
              />
            ))}
          </div>
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
