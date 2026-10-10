import type { MemoryEntry } from '@hermes/shared'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n'
import { Brain } from '@/lib/icons'

import { ActivityEmpty } from '../activity/activity-empty'
import { ListRow, ListRowSkeleton, SettingsSection } from '../primitives'

import { type MemoryTarget, useMemoryNotes } from './use-memory-notes'

const TARGETS: MemoryTarget[] = ['memory', 'user']

/** Notes the assistant keeps, per target. Forget confirms first; remember trims and refuses blanks. */
export function MemoryNotes() {
  const { t } = useI18n()
  const c = t.settings.activity
  const { entries, targets, status, notice, reload, remember, forget } = useMemoryNotes()
  const [target, setTarget] = useState<MemoryTarget>('memory')
  const [draft, setDraft] = useState('')
  const [pendingForget, setPendingForget] = useState<MemoryEntry | null>(null)
  const targetEnabled = targets[target] !== 'disabled'
  const canRemember = targetEnabled && draft.trim().length > 0

  if (status === 'error' && entries.length === 0) {
    return (
      <SettingsSection icon={Brain} title={c.memoryTitle}>
        <ErrorState title={c.memoryLoadFailed}>
          <Button onClick={() => void reload()} variant="outline">
            {c.retry}
          </Button>
        </ErrorState>
      </SettingsSection>
    )
  }

  const loading = status === 'loading' && entries.length === 0

  return (
    <>
      <SettingsSection icon={Brain} title={c.memoryTitle}>
        {loading ? <ListRowSkeleton /> : null}

        {TARGETS.map(group => {
          const groupEntries = entries.filter(entry => entry.target === group)
          const groupEnabled = targets[group] !== 'disabled'

          return (
            <div className="grid gap-1" key={group}>
              <p className="px-1 text-xs font-medium text-muted-foreground">
                {group === 'memory' ? c.memoryAgentNotes : c.memoryAboutYou}
              </p>
              {!loading && !groupEnabled ? <ActivityEmpty>{c.memoryDisabled}</ActivityEmpty> : null}
              {!loading && groupEnabled && groupEntries.length === 0 ? (
                <ActivityEmpty>{c.memoryEmpty}</ActivityEmpty>
              ) : null}
              {groupEntries.map(entry => (
                <ListRow
                  action={
                    <Button onClick={() => setPendingForget(entry)} variant="outline">
                      {c.memoryForget}
                    </Button>
                  }
                  key={`${entry.target}-${entry.index}`}
                  title={entry.text}
                />
              ))}
            </div>
          )
        })}

        <form
          className="grid gap-2 pt-2"
          onSubmit={event => {
            event.preventDefault()

            if (!canRemember) {
              return
            }

            // The draft survives a refused write, so the user can retry without retyping.
            void remember(target, draft.trim()).then(saved => {
              if (saved) {
                setDraft('')
              }
            })
          }}
        >
          <SegmentedControl<MemoryTarget>
            onChange={setTarget}
            options={[
              { id: 'memory', label: c.memoryAgentNotes },
              { id: 'user', label: c.memoryAboutYou }
            ]}
            value={target}
          />
          <div className="flex items-center gap-2">
            <Input
              disabled={!targetEnabled}
              onChange={event => setDraft(event.target.value)}
              placeholder={c.memoryAddPlaceholder}
              value={draft}
            />
            <Button disabled={!canRemember} type="submit" variant="outline">
              {c.memoryAdd}
            </Button>
          </div>
        </form>

        {notice ? (
          <p className="px-1 text-sm text-destructive" role="alert">
            {notice === 'changed' ? c.memoryChanged : c.memorySaveFailed}
          </p>
        ) : null}
      </SettingsSection>

      <ConfirmDialog
        cancelLabel={t.common.cancel}
        confirmLabel={c.memoryForgetConfirm}
        description={c.memoryForgetDescription}
        destructive
        dismissOnConfirm
        onClose={() => setPendingForget(null)}
        onConfirm={() => (pendingForget ? forget(pendingForget) : undefined)}
        open={pendingForget !== null}
        title={c.memoryForgetTitle}
      />
    </>
  )
}
