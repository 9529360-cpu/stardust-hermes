import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { AutomationBlueprint, AutomationBlueprintField } from '@/hermes'
import type { Translations } from '@/i18n'

// The blueprint catalog is shared with the dashboard, so its deliver slot
// defaults to "origin" (the chat/home-channel a dashboard or gateway job was
// created from). Desktop has no origin chat, so seed the deliver slot to the
// desktop's native target ("local" = This desktop) instead. The dialog then
// renders that slot with the shared DeliverSelect (backend-sourced targets), so
// the raw "origin" option never reaches the desktop UI.
const DELIVER_FIELD = 'deliver'
const DESKTOP_DELIVER_DEFAULT = 'local'

type CronCopy = Translations['cron']

export function blueprintDisplayTitle(blueprint: AutomationBlueprint, c: CronCopy): string {
  return c.blueprints.catalog[blueprint.key]?.title ?? blueprint.title
}

export function blueprintDisplayDescription(blueprint: AutomationBlueprint, c: CronCopy): string {
  return c.blueprints.catalog[blueprint.key]?.description ?? blueprint.description
}

function blueprintFieldOverride(blueprintKey: string, fieldName: string, c: CronCopy) {
  return c.blueprints.catalog[blueprintKey]?.fields?.[fieldName]
}

export function blueprintDisplayFieldLabel(
  blueprintKey: string,
  field: AutomationBlueprintField,
  c: CronCopy
): string {
  return blueprintFieldOverride(blueprintKey, field.name, c)?.label ?? c.blueprints.fieldLabels[field.name] ?? field.label
}

export function blueprintDisplayFieldHelp(
  blueprintKey: string,
  field: AutomationBlueprintField,
  c: CronCopy
): string | undefined {
  return blueprintFieldOverride(blueprintKey, field.name, c)?.help ?? c.blueprints.fieldHelp[field.name] ?? blueprintSlotHelp(field)
}

function blueprintDisplayFieldPlaceholder(blueprintKey: string, field: AutomationBlueprintField, c: CronCopy): string {
  return (
    blueprintFieldOverride(blueprintKey, field.name, c)?.help ??
    c.blueprints.fieldHelp[field.name] ??
    field.help ??
    blueprintDisplayFieldLabel(blueprintKey, field, c)
  )
}

export function blueprintDisplayOption(option: string, c: CronCopy): string {
  return c.blueprints.optionLabels[option] ?? option
}

function isDeliverField(field: AutomationBlueprintField): boolean {
  return field.name === DELIVER_FIELD
}

// Initial form state for a blueprint = each field's default (or ''). Pure so the
// suite can assert the form seeds correctly without mounting React. The deliver
// slot is special-cased: an "origin" default (or empty) becomes "local" so a
// desktop-created job delivers to This desktop instead of nowhere.
export function initialBlueprintValues(blueprint: AutomationBlueprint): Record<string, string> {
  const out: Record<string, string> = {}

  for (const field of blueprint.fields) {
    const seeded = field.default ?? ''
    out[field.name] = isDeliverField(field) && (seeded === '' || seeded === 'origin') ? DESKTOP_DELIVER_DEFAULT : seeded
  }

  return out
}

// A slot-level validation error from the backend arrives as "422: <message>"
// (or "<code>: <message>"); strip the leading numeric code for inline display.
export function cleanBlueprintFieldError(message: string): string {
  return message.replace(/^\d+:\s*/, '')
}

// Help text to show under a slot control. The backend deliver help is
// origin/dashboard-centric and even contradicts desktop semantics ("local =
// save only" vs. This desktop), and the DeliverSelect is self-explanatory —
// skip it for the deliver slot.
export function blueprintSlotHelp(field: AutomationBlueprintField): string | undefined {
  return field.help && field.type !== 'text' && !isDeliverField(field) ? field.help : undefined
}

// Renders one blueprint slot's control (enum/weekdays → Select, time → time
// input, else text). The deliver slot is handled separately by the dialog's
// shared DeliverSelect, so it's not rendered here.
export function BlueprintSlotControl({
  blueprintKey,
  c,
  field,
  id,
  onChange,
  value
}: {
  blueprintKey: string
  c: CronCopy
  field: AutomationBlueprintField
  id: string
  onChange: (next: string) => void
  value: string
}) {
  if (field.type === 'enum' || field.type === 'weekdays') {
    return (
      <Select onValueChange={onChange} value={value}>
        <SelectTrigger className="h-9 rounded-md" id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {field.options.map(option => (
            <SelectItem key={option} value={option}>
              {blueprintDisplayOption(option, c)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  if (field.type === 'time') {
    return <Input id={id} onChange={event => onChange(event.target.value)} type="time" value={value} />
  }

  return (
    <Input
      id={id}
      onChange={event => onChange(event.target.value)}
      placeholder={blueprintDisplayFieldPlaceholder(blueprintKey, field, c)}
      type="text"
      value={value}
    />
  )
}
