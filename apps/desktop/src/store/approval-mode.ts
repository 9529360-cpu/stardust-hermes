import { atom } from 'nanostores'

export type ApprovalMode = 'manual' | 'off' | 'smart'
/** What the cache can say about a profile. A profile with no entry is still loading;
 *  `unknown` means a read or write failed and no mode was ever confirmed for it. */
export type ApprovalModeReading = ApprovalMode | 'unknown'
export type ApprovalModeRequester = (method: string, params?: Record<string, unknown>) => Promise<unknown>

const APPROVAL_MODES = new Set<ApprovalMode>(['manual', 'smart', 'off'])
const revisions = new Map<string, number>()
const confirmedModes = new Map<string, ApprovalMode>()

export const $approvalModes = atom<Record<string, ApprovalModeReading>>({})

function profileKey(profile: string): string {
  return profile.trim() || 'default'
}

function nextRevision(profile: string): number {
  const revision = (revisions.get(profile) ?? 0) + 1
  revisions.set(profile, revision)

  return revision
}

/** Null when the value is not a mode the backend can mean. Never a default: an
 *  unreadable value must not be shown as one of the modes. */
function parseApprovalMode(value: unknown): ApprovalMode | null {
  const normalized = String(value ?? '')
    .trim()
    .toLowerCase()

  return APPROVAL_MODES.has(normalized as ApprovalMode) ? (normalized as ApprovalMode) : null
}

export function approvalModeForProfile(profile: string): ApprovalModeReading | undefined {
  return $approvalModes.get()[profileKey(profile)]
}

function cacheApprovalMode(profile: string, reading: ApprovalModeReading): void {
  const key = profileKey(profile)
  $approvalModes.set({ ...$approvalModes.get(), [key]: reading })
}

/** A failed read or write keeps the last mode the backend confirmed. With none, the
 *  profile is unknown rather than shown as a default. */
function cacheUnconfirmed(profile: string): void {
  cacheApprovalMode(profile, confirmedModes.get(profileKey(profile)) ?? 'unknown')
}

/** A backend event that does not name a mode says nothing about it, so it is ignored. */
export function reconcileApprovalModeForProfile(profile: string, value: unknown): ApprovalMode | undefined {
  const key = profileKey(profile)
  const mode = parseApprovalMode(value)

  if (!mode) {
    return undefined
  }

  nextRevision(key)
  confirmedModes.set(key, mode)
  cacheApprovalMode(key, mode)

  return mode
}

export async function syncApprovalModeForProfile(
  requestGateway: ApprovalModeRequester,
  profile: string
): Promise<ApprovalMode> {
  const key = profileKey(profile)
  const revision = nextRevision(key)

  try {
    const result = (await requestGateway('config.get', { key: 'approvals.mode' })) as { value?: unknown } | undefined
    const mode = parseApprovalMode(result?.value)

    if (!mode) {
      throw new Error('approvals.mode could not be read')
    }

    if (revisions.get(key) === revision) {
      confirmedModes.set(key, mode)
      cacheApprovalMode(key, mode)
    }

    return mode
  } catch (error) {
    if (revisions.get(key) === revision) {
      cacheUnconfirmed(key)
    }

    throw error
  }
}

export async function setApprovalModeForProfile(
  requestGateway: ApprovalModeRequester,
  profile: string,
  mode: ApprovalMode
): Promise<ApprovalMode> {
  const key = profileKey(profile)
  const revision = nextRevision(key)
  cacheApprovalMode(key, mode)

  try {
    const result = (await requestGateway('config.set', {
      key: 'approvals.mode',
      value: mode
    })) as { value?: unknown } | undefined

    // An accepted write that echoes no mode confirms the one the user chose.
    const authoritative = parseApprovalMode(result?.value) ?? mode

    if (revisions.get(key) === revision) {
      confirmedModes.set(key, authoritative)
      cacheApprovalMode(key, authoritative)
    }

    return authoritative
  } catch (error) {
    if (revisions.get(key) === revision) {
      cacheUnconfirmed(key)
    }

    throw error
  }
}
