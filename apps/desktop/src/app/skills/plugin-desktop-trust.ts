import type { PluginRecord } from '@/contrib/plugins-store'
import type { ConfirmRequest } from '@/store/confirm'

export interface DesktopPluginTrustCopy {
  confirmLabel: string
  description: (source: string, pinnedSha: string) => string
  title: (name: string) => string
}

export interface DesktopPluginTrustDeps {
  confirm: (request: ConfirmRequest) => Promise<boolean>
  setEnabled: (id: string, enabled: boolean) => Promise<void>
}

export function desktopPluginNeedsExplicitTrust(record: PluginRecord): boolean {
  return record.kind !== 'bundled'
}

export function desktopPluginTrustSource(record: PluginRecord): { pinnedSha: string; source: string } {
  const pinnedSha = record.packageOrigin?.sha?.trim() ?? ''
  const source = record.packageOrigin?.repo?.trim() || record.file?.trim() || record.id

  return { pinnedSha, source }
}

/**
 * Enable/disable one Desktop plugin without inventing a second trust state.
 *
 * Persisted plugin decisions remain owned by plugins-store. This helper only
 * inserts the explicit trust ceremony before non-bundled code crosses from
 * inventoried/inert to active in the renderer.
 */
export async function setDesktopPluginEnabledWithTrust(
  record: PluginRecord,
  displayName: string,
  enabled: boolean,
  copy: DesktopPluginTrustCopy,
  deps: DesktopPluginTrustDeps
): Promise<boolean> {
  if (enabled && desktopPluginNeedsExplicitTrust(record)) {
    const { pinnedSha, source } = desktopPluginTrustSource(record)
    const approved = await deps.confirm({
      confirmLabel: copy.confirmLabel,
      description: copy.description(source, pinnedSha),
      destructive: true,
      title: copy.title(displayName)
    })

    if (!approved) {
      return false
    }
  }

  await deps.setEnabled(record.id, enabled)

  return true
}
