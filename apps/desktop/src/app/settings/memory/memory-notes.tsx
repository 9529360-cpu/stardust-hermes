import type { MemoryEntry } from '@hermes/shared'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n'
import type { Translations } from '@/i18n/types'
import { Brain } from '@/lib/icons'

import { ActivityEmpty } from '../activity/activity-empty'
import { ListRow, ListRowSkeleton, SettingsSection } from '../primitives'

import { type MemoryTarget, useMemoryNotes } from './use-memory-notes'

type ActivityCopy = Translations['settings']['activity']

/** About you comes first: it is what the assistant most often needs. */
const TARGETS: MemoryTarget[] = ['user', 'memory']

function targetCopy(target: MemoryTarget, c: ActivityCopy) {
  return target === 'user'
    ? { label: c.memoryAboutYou, hint: c.memoryAboutYouHint, placeholder: c.memoryPlaceholderUser }
    : { label: c.memoryNotes, hint: c.memoryNotesHint, placeholder: c.memoryPlaceholderNotes }
}

/**
 * What the assistant remembers about you and about your work. Anything can be added or forgotten.
 * Forgetting asks first, and it only ever removes the note the user is looking at.
 */
export function MemoryNotes() {
  const { t } = useI18n()
  const c = t.settings.activity
  const { entries, targets, status, notice, reload, remember, forget } = useMemoryNotes()
  const [target, setTarget] = useState<MemoryTarget>('user')
  const [draft, setDraft] = useState('')
  const [pendingForget, setPendingForget] = useState<MemoryEntry | null>(null)
  const active = targetCopy(target, c)
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
        <p className="px-1 pb-1 text-sm text-muted-foreground">{c.memoryIntro}</p>
        {loading ? <ListRowSkeleton /> : null}

        {TARGETS.map(group => {
          const copy = targetCopy(group, c)
          const groupEntries = entries.filter(entry => entry.target === group)
          const groupEnabled = targets[group] !== 'disabled'

          return (
            <div className="grid gap-0.5" key={group}>
              <div className="px-1 pt-2">
                <p className="text-sm font-medium">{copy.label}</p>
                <p className="text-xs text-muted-foreground">{copy.hint}</p>
              </div>
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
          className="grid gap-2 pt-3"
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
              { id: 'user', label: c.memoryAboutYou },
              { id: 'memory', label: c.memoryNotes }
            ]}
            value={target}
          />
          <div className="flex items-center gap-2">
            <Input
              aria-label={active.placeholder}
              disabled={!targetEnabled}
              onChange={event => setDraft(event.target.value)}
              placeholder={active.placeholder}
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
