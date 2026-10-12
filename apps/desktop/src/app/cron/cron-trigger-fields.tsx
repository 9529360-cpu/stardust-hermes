import { Button } from '@/components/ui/button'
import { Field, FieldHint } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { SegmentedControl, type SegmentedControlOption } from '@/components/ui/segmented-control'
import type { Translations } from '@/i18n'
import { selectDesktopPaths } from '@/lib/desktop-fs'
import { notifyError } from '@/store/notifications'
import type { CronTriggerType } from '@/types/hermes'

import type { CronTriggerFormValues } from './cron-trigger-model'

type CronCopy = Translations['cron']

/** True when this window has the desktop bridge, so the folder picker can open. */
export function canPickWatchFolder(): boolean {
  return typeof window !== 'undefined' && Boolean(window.stardustDesktop)
}

/** "When to run": scheduled, every start, or a new file in a folder. */
export function CronWhenChoice({
  c,
  onChange,
  value
}: {
  c: CronCopy
  onChange: (next: CronTriggerType) => void
  value: CronTriggerType
}) {
  const options: SegmentedControlOption<CronTriggerType>[] = [
    { id: 'schedule', label: c.trigger.whenSchedule },
    { id: 'startup', label: c.trigger.whenStartup },
    { id: 'folder', label: c.trigger.whenFolder }
  ]

  return <SegmentedControl onChange={onChange} options={options} value={value} />
}

/** A folder path with the desktop picker beside it. The field stays editable, so a path can be typed. */
export function WatchFolderInput({
  c,
  id,
  invalid,
  onChange,
  placeholder,
  value
}: {
  c: CronCopy
  id: string
  invalid: boolean
  onChange: (next: string) => void
  placeholder: string
  value: string
}) {
  async function pickFolder() {
    try {
      const [dir] = await selectDesktopPaths({
        defaultPath: value.trim() || undefined,
        directories: true,
        multiple: false,
        title: c.trigger.folderPickerTitle
      })

      if (dir) {
        onChange(dir)
      }
    } catch (err) {
      notifyError(err, c.trigger.pickFailed)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Input
        aria-invalid={invalid || undefined}
        className="font-mono"
        id={id}
        onChange={event => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
      {canPickWatchFolder() && (
        <Button onClick={() => void pickFolder()} size="sm" type="button" variant="outline">
          {c.trigger.folderChoose}
        </Button>
      )}
    </div>
  )
}

/** Folder and pattern fields for a folder job, with the one-line deny-list note and the privacy note. */
export function CronTriggerFolderFields({
  c,
  folderInvalid,
  onChange,
  values
}: {
  c: CronCopy
  folderInvalid: boolean
  onChange: (next: CronTriggerFormValues) => void
  values: CronTriggerFormValues
}) {
  return (
    <div className="grid gap-4">
      <Field htmlFor="cron-watch-folder" label={c.trigger.folderLabel}>
        <WatchFolderInput
          c={c}
          id="cron-watch-folder"
          invalid={folderInvalid}
          onChange={watchDir => onChange({ ...values, watchDir })}
          placeholder={c.trigger.folderPlaceholder}
          value={values.watchDir}
        />
        <FieldHint>{c.trigger.folderRestriction}</FieldHint>
      </Field>

      <Field htmlFor="cron-watch-pattern" label={c.trigger.patternLabel}>
        <Input
          className="font-mono"
          id="cron-watch-pattern"
          onChange={event => onChange({ ...values, pattern: event.target.value })}
          placeholder={c.trigger.patternPlaceholder}
          value={values.pattern}
        />
        <FieldHint>{c.trigger.patternHint}</FieldHint>
        <FieldHint>{c.trigger.folderHint}</FieldHint>
      </Field>
    </div>
  )
}
