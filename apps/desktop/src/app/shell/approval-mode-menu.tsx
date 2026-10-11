import { useStore } from '@nanostores/react'
import { useEffect, useMemo } from 'react'

import type { StatusbarItem } from '@/app/shell/statusbar-controls'
import {
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator
} from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { Shield, ShieldOff } from '@/lib/icons'
import {
  $approvalModes,
  type ApprovalMode,
  type ApprovalModeReading,
  type ApprovalModeRequester,
  setApprovalModeForProfile,
  syncApprovalModeForProfile
} from '@/store/approval-mode'

/** The approvals item. It reads the same mode wherever the status bar shows: a profile's
 *  mode is read once the gateway is open, shown as a reading label until then, and shown as
 *  unknown when the read fails. It never shows a default mode the user did not set. */
export function useApprovalModeStatusbarItem(
  profile: string,
  requestGateway: ApprovalModeRequester,
  gatewayOpen: boolean
): StatusbarItem {
  const { t } = useI18n()
  const copy = t.shell.approvalMode
  // The bar names the subject, then shows the mode beside it, the way the gateway
  // item does. A bare mode name such as "Off" reads as an action, not a state.
  const subject = t.shell.statusbar.toggleApprovalMode
  const modes = useStore($approvalModes)
  const reading: ApprovalModeReading | undefined = modes[profile.trim() || 'default']

  const labels = useMemo<Record<ApprovalModeReading, string>>(
    () => ({ manual: copy.manual, smart: copy.smart, off: copy.off, unknown: copy.unknown }),
    [copy.manual, copy.off, copy.smart, copy.unknown]
  )

  const descriptions = useMemo<Record<ApprovalMode, string>>(
    () => ({
      manual: copy.manualDescription,
      smart: copy.smartDescription,
      off: copy.offDescription
    }),
    [copy.manualDescription, copy.offDescription, copy.smartDescription]
  )

  // Reads wait for the gateway: a read sent while it connects fails and would leave
  // the item unknown until the next profile switch.
  useEffect(() => {
    if (!gatewayOpen) {
      return
    }

    void syncApprovalModeForProfile(requestGateway, profile).catch(() => undefined)
  }, [gatewayOpen, profile, requestGateway])

  const detail = reading === undefined ? t.shell.statusbar.reading : labels[reading]

  return {
    className: reading === 'off' ? 'bg-(--chrome-action-hover) text-foreground' : undefined,
    detail,
    icon: reading === 'off' ? <ShieldOff className="size-3.5" /> : <Shield className="size-3.5 opacity-70" />,
    id: 'approval-mode',
    label: subject,
    menuAlign: 'end',
    menuClassName: 'w-72 p-1',
    menuContent: (
      <>
        <DropdownMenuLabel>{copy.title}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          onValueChange={value => {
            void setApprovalModeForProfile(requestGateway, profile, value as ApprovalMode).catch(() => undefined)
          }}
          value={reading === 'unknown' || reading === undefined ? '' : reading}
        >
          {(['manual', 'smart', 'off'] as const).map(value => (
            <DropdownMenuRadioItem className="items-start gap-2" key={value} value={value}>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="text-xs text-foreground">{labels[value]}</span>
                <span className="text-[0.6875rem] leading-snug text-(--ui-text-tertiary)">{descriptions[value]}</span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </>
    ),
    title: copy.ariaLabel(detail),
    variant: 'menu'
  }
}
