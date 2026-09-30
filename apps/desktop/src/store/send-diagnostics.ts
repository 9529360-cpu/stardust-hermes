// "Send Diagnostics" — Stardust-owned local diagnostics handoff.
//
// Flow: an error card opens the consent modal -> the backend prepares a
// force-redacted, short-lived ZIP in its own profile cache -> Electron downloads
// that file through the existing authenticated gateway-file bridge -> the user
// chooses the local save destination -> Desktop asks the backend to discard its
// temporary copy. Nothing in this default flow is uploaded to a support service.
//
// The legacy diagnostics.share_nous RPC remains backend-compatible for older
// clients, but this Stardust Desktop surface never calls it.
import { atom } from 'nanostores'

import type { HermesGateway } from '@/hermes'
import { $gateway } from '@/store/gateway'
import { $connection } from '@/store/session'

export interface SendDiagnosticsResult {
  byteSize?: number
  cleanupWarning?: string
  filename?: string
  savedPath?: string
}

export interface SendDiagnosticsState {
  /** Short text describing the failure that prompted the report (attached
   *  to the bundle as error-context.txt, force-redacted server-side). */
  errorContext?: string
  error?: string
  phase: 'consent' | 'done' | 'error' | 'preparing'
  result?: SendDiagnosticsResult
}

export const $sendDiagnostics = atom<SendDiagnosticsState | null>(null)

// Generation token: bumped on every open AND every dismiss. An in-flight
// prepare/save captures the generation it started under and only writes its
// completion back when the token still matches, so dismissing mid-flight is
// immediate and a stale completion cannot resurrect or overwrite the dialog.
let generation = 0

/** Open the consent modal. No bundle collection or network I/O happens yet. */
export function requestSendDiagnostics(errorContext?: string): void {
  generation += 1
  $sendDiagnostics.set({ errorContext, phase: 'consent' })
}

export function dismissSendDiagnostics(): void {
  generation += 1
  $sendDiagnostics.set(null)
}

interface PrepareBundleResponse {
  byte_size?: number
  error?: string
  filename?: string
  ok: boolean
  path?: string
}

interface DiscardBundleResponse {
  error?: string
  ok: boolean
  removed: boolean
}

/** Read the LOCAL desktop log via Electron so a remote backend's bundle still
 *  carries the Desktop-side transport evidence. Best-effort: absence of the
 *  IPC (browser dashboard, older shells) just omits the file. */
async function collectLocalExtras(): Promise<Record<string, string>> {
  try {
    const logs = await window.hermesDesktop?.getRecentLogs?.()
    const lines = Array.isArray(logs?.lines) ? logs.lines : []

    return lines.length ? { 'desktop.log': lines.join('\n') } : {}
  } catch {
    return {}
  }
}

const PREPARE_TIMEOUT_MS = 120_000
const DISCARD_TIMEOUT_MS = 30_000

async function discardPreparedBundle(
  gateway: HermesGateway,
  path: string
): Promise<string | undefined> {
  try {
    const response = await gateway.request<DiscardBundleResponse>(
      'diagnostics.discard_bundle',
      { path },
      DISCARD_TIMEOUT_MS
    )

    return response.ok ? undefined : response.error || 'temporary diagnostics cleanup failed'
  } catch (error) {
    return error instanceof Error ? error.message : String(error)
  }
}

/** User confirmed — prepare locally, save through Electron, then delete backend temp copy. */
export async function confirmSendDiagnostics(): Promise<void> {
  const current = $sendDiagnostics.get()

  if (!current || current.phase !== 'consent') {
    return
  }

  const startedGeneration = generation
  const stillCurrent = () => generation === startedGeneration
  const gateway = $gateway.get()
  // Freeze the gateway owner coordinate for the whole prepare -> download ->
  // discard transaction. The user may switch active connection/profile while
  // the backend is building the ZIP; downloading that old-backend path through
  // the newly active connection would cross ownership boundaries.
  const connection = $connection.get()

  if (!gateway) {
    $sendDiagnostics.set({ ...current, error: 'Hermes gateway unavailable', phase: 'error' })

    return
  }

  $sendDiagnostics.set({ ...current, phase: 'preparing' })

  let preparedPath: string | undefined

  try {
    const extraFiles = await collectLocalExtras()

    if (!stillCurrent()) {
      return
    }

    const prepared = await gateway.request<PrepareBundleResponse>(
      'diagnostics.prepare_bundle',
      {
        ...(current.errorContext ? { error_context: current.errorContext } : {}),
        ...(Object.keys(extraFiles).length ? { extra_files: extraFiles } : {})
      },
      PREPARE_TIMEOUT_MS
    )

    if (!prepared.ok || !prepared.path) {
      throw new Error(prepared.error || 'diagnostics bundle preparation failed')
    }

    preparedPath = prepared.path

    if (!stillCurrent()) {
      await discardPreparedBundle(gateway, preparedPath)

      return
    }

    const saveGatewayFile = window.hermesDesktop?.saveGatewayFile

    if (!saveGatewayFile) {
      throw new Error('Desktop file save bridge is unavailable')
    }

    const saved = await saveGatewayFile({
      connectionId: connection?.connectionId,
      path: preparedPath,
      profile: connection?.profile,
      suggestedName: prepared.filename || 'stardust-diagnostics.zip'
    })

    const cleanupWarning = await discardPreparedBundle(gateway, preparedPath)

    preparedPath = undefined

    if (!stillCurrent()) {
      return
    }

    if (saved.canceled || !saved.saved) {
      if (cleanupWarning) {
        $sendDiagnostics.set({
          ...current,
          error: `Save canceled. Temporary backend cleanup could not be confirmed: ${cleanupWarning}`,
          phase: 'error'
        })
      } else {
        dismissSendDiagnostics()
      }

      return
    }

    $sendDiagnostics.set({
      ...current,
      phase: 'done',
      result: {
        byteSize: prepared.byte_size,
        cleanupWarning,
        filename: prepared.filename,
        savedPath: saved.path
      }
    })
  } catch (error) {
    let cleanupWarning: string | undefined

    if (preparedPath) {
      cleanupWarning = await discardPreparedBundle(gateway, preparedPath)
    }

    if (!stillCurrent()) {
      return
    }

    const detail = error instanceof Error ? error.message : String(error)
    const cleanupSuffix = cleanupWarning
      ? ` Temporary backend cleanup could not be confirmed: ${cleanupWarning}`
      : ''

    $sendDiagnostics.set({
      ...current,
      error: `${detail}${cleanupSuffix}`,
      phase: 'error'
    })
  }
}
