import fs from 'node:fs'
import path from 'node:path'

/**
 * The venv folder of a Stardust install root is `venv` or `.venv`.
 *
 * `uv venv` defaults to `.venv` while the installers write `venv`, so both layouts exist in the wild.
 * This mirrors `hermes_cli/venv_layout.py` (resolve_venv_dir, ensure_venv_alias) and
 * `scripts/desktop-update/windows.ps1` (Resolve-VenvDir, Ensure-VenvAlias); keep the three in lockstep.
 */

export const DEFAULT_VENV_NAME = 'venv'
export const UV_VENV_NAME = '.venv'

export interface VenvLayoutDeps {
  /** Host verdict; defaults to process.platform. */
  isWindows?: boolean
  /** Interpreter probe; defaults to a stat that follows junctions and symlinks. */
  isFile?: (candidate: string) => boolean
}

function interpreterParts(isWindows: boolean): string[] {
  return isWindows ? ['Scripts', 'python.exe'] : ['bin', 'python']
}

function statIsFile(candidate: string): boolean {
  try {
    return fs.statSync(candidate).isFile()
  } catch {
    return false
  }
}

/**
 * `<root>/venv` when it holds the interpreter, else `<root>/.venv` when that does, else `<root>/venv`
 * (the fresh-install default).
 */
export function resolveVenvDir(updateRoot: string, deps: VenvLayoutDeps = {}): string {
  const isWindows = deps.isWindows ?? process.platform === 'win32'
  const isFile = deps.isFile ?? statIsFile
  const interpreter = interpreterParts(isWindows)

  for (const name of [DEFAULT_VENV_NAME, UV_VENV_NAME]) {
    const dir = path.join(updateRoot, name)

    if (isFile(path.join(dir, ...interpreter))) {
      return dir
    }
  }

  return path.join(updateRoot, DEFAULT_VENV_NAME)
}

export type VenvAliasOutcome = 'created' | 'present' | 'no-dotvenv' | 'failed'

export interface VenvAliasDeps {
  /** Host verdict; defaults to process.platform. */
  isWindows?: boolean
  /** True when any entry (directory, junction, file or dangling link) exists at the path. */
  entryExists?: (candidate: string) => boolean
  isDirectory?: (candidate: string) => boolean
  /** Creates the link. Node refuses to replace an existing entry, so a race cannot overwrite one. */
  createLink?: (target: string, linkPath: string, isWindows: boolean) => void
  /** Receives the reason when the alias could not be created. */
  log?: (message: string) => void
}

function entryExistsAt(candidate: string): boolean {
  try {
    fs.lstatSync(candidate)

    return true
  } catch {
    return false
  }
}

function statIsDirectory(candidate: string): boolean {
  try {
    return fs.statSync(candidate).isDirectory()
  } catch {
    return false
  }
}

function createFsLink(target: string, linkPath: string, isWindows: boolean): void {
  if (isWindows) {
    // A directory junction, like `mklink /J`: no elevation needed, unlike a directory symlink.
    fs.symlinkSync(target, linkPath, 'junction')

    return
  }

  // Relative, so the link keeps working if the install folder is moved.
  fs.symlinkSync(path.relative(path.dirname(linkPath), target), linkPath, 'dir')
}

/**
 * Expose a `.venv`-only install under `venv` too, so tools that still hardcode `venv` keep working.
 *
 * Creates `venv` only when no entry of that name exists at all, and `.venv` is a directory. An existing
 * `venv` (directory, junction, file or dangling link) is never touched, and `.venv` is never modified.
 * Never throws: a failure is logged and the resolver still finds `.venv`.
 */
export function ensureVenvAlias(updateRoot: string, deps: VenvAliasDeps = {}): VenvAliasOutcome {
  const isWindows = deps.isWindows ?? process.platform === 'win32'
  const entryExists = deps.entryExists ?? entryExistsAt
  const isDirectory = deps.isDirectory ?? statIsDirectory
  const createLink = deps.createLink ?? createFsLink
  const alias = path.join(updateRoot, DEFAULT_VENV_NAME)
  const target = path.join(updateRoot, UV_VENV_NAME)

  if (entryExists(alias)) {
    return 'present'
  }

  if (!isDirectory(target)) {
    return 'no-dotvenv'
  }

  try {
    createLink(target, alias, isWindows)

    return 'created'
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)

    deps.log?.(`could not create the venv alias ${alias} -> ${target} (${reason}); continuing with .venv`)

    return 'failed'
  }
}

export interface ManagedShimRefreshCommand {
  command: string
  args: string[]
  cwd: string
}

/**
 * Desktop start hook for the managed `stardust` / `hermes` shims. The shim writer is the Python
 * `hermes_cli._install_repair` module, run on the install's own interpreter, so there is one writer.
 * Null off Windows (the shims are Windows-only) or before the install has a Windows interpreter.
 */
export function managedShimRefreshCommand(
  updateRoot: string,
  deps: VenvLayoutDeps = {}
): ManagedShimRefreshCommand | null {
  const isWindows = deps.isWindows ?? process.platform === 'win32'

  if (!isWindows) {
    return null
  }

  const isFile = deps.isFile ?? statIsFile
  const python = path.join(resolveVenvDir(updateRoot, { isWindows, isFile }), 'Scripts', 'python.exe')

  if (!isFile(python)) {
    return null
  }

  return {
    command: python,
    args: ['-m', 'hermes_cli._install_repair', '--refresh-managed-shims', updateRoot],
    cwd: updateRoot
  }
}
