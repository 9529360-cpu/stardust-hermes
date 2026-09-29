/** When a composer attachment must cross as BYTES rather than ride its path.
 *
 * Shared by submit-time attachment staging and drop-time routing so both
 * surfaces make the same filesystem-boundary decision.
 */

import { isWindowsAbsolutePath } from '@/lib/path-compare'

const POSIX_ABSOLUTE_PATH_RE = /^\/(?!\/)/

/** Terminal backends whose execution environment has its own filesystem. */
export const CONTAINER_TERMINAL_BACKENDS = new Set([
  'docker',
  'ssh',
  'singularity',
  'modal',
  'daytona',
  'vercel_sandbox'
])

/**
 * A locally launched gateway can still target a filesystem the Desktop cannot
 * address directly (container/SSH/etc., or Windows host -> POSIX backend).
 */
export function attachmentPathNeedsUpload(
  path: string,
  backendCwd?: null | string,
  terminalBackend?: null | string
): boolean {
  if (CONTAINER_TERMINAL_BACKENDS.has((terminalBackend || '').trim().toLowerCase())) {
    return true
  }

  return isWindowsAbsolutePath(path.trim()) && POSIX_ABSOLUTE_PATH_RE.test(backendCwd?.trim() || '')
}
