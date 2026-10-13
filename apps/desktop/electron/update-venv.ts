import { execFileSync } from 'node:child_process'
import { existsSync, realpathSync, statSync } from 'node:fs'
import path from 'node:path'

export type UpdateInstallationSource = 'managed' | 'development' | 'unknown'
export type UpdateRuntimeMode = 'managed' | 'development' | 'unknown'

const UPDATE_REQUIRED_MODULES = ['fastapi', 'uvicorn', 'pydantic', 'openai', 'yaml', 'hermes_cli.main'] as const

export interface UpdateVenvTarget {
  root: string | null
  python: string | null
  launcher: string | null
  environmentType: UpdateInstallationSource | null
  error: string | null
}

export interface ResolveUpdateVenvOptions {
  runtimePython?: string | null
  virtualEnv?: string | null
  installationSource: UpdateInstallationSource
  runtimeMode: UpdateRuntimeMode
  platform?: NodeJS.Platform
}

export function resolveUpdateVenv(projectRoot: string, options: ResolveUpdateVenvOptions): UpdateVenvTarget {
  const root = path.resolve(projectRoot)
  const windows = (options.platform ?? process.platform) === 'win32'
  const scriptsDir = windows ? 'Scripts' : 'bin'
  const pythonName = windows ? 'python.exe' : 'python'
  const launcherName = windows ? 'stardust.exe' : 'stardust'

  const candidates = ['.venv', 'venv'].map((name) => {
    const envRoot = path.join(root, name)

    return {
      name,
      root: envRoot,
      python: path.join(envRoot, scriptsDir, pythonName),
      launcher: path.join(envRoot, scriptsDir, launcherName)
    }
  }).filter((candidate) => {
    try {
      return statSync(candidate.root).isDirectory()
    } catch {
      return false
    }
  })

  const fail = (message: string): UpdateVenvTarget => ({
    root: null,
    python: null,
    launcher: null,
    environmentType: null,
    error: message
  })

  if (candidates.length === 0) {return fail(`No .venv or venv directory exists under ${root}.`)}

  const matchPath = (value: string | null | undefined, candidatesToMatch: typeof candidates): (typeof candidates)[number] | undefined => {
    if (!value) {return undefined}
    const normalized = path.normalize(path.resolve(value)).toLowerCase()

    return candidatesToMatch.find((candidate) => path.normalize(path.resolve(candidate.python)).toLowerCase() === normalized)
  }

  let selected = matchPath(options.runtimePython, candidates)

  if (options.runtimePython && !selected) {
    return fail(`The active Python ${options.runtimePython} is outside the project's .venv and venv environments.`)
  }

  if (!selected && options.virtualEnv) {
    const activeRoot = path.normalize(path.resolve(options.virtualEnv)).toLowerCase()
    selected = candidates.find((candidate) => path.normalize(path.resolve(candidate.root)).toLowerCase() === activeRoot)
  }

  const selectionSource = options.installationSource !== 'unknown' ? options.installationSource : options.runtimeMode

  if (!selected && candidates.length === 1) {selected = candidates[0]}

  if (!selected && selectionSource !== 'unknown') {
    const name = selectionSource === 'managed' ? 'venv' : '.venv'
    selected = candidates.find((candidate) => candidate.name === name)
  }

  if (!selected) {return fail(`Environment selection is ambiguous: both .venv and venv exist under ${root}, but the active runtime identity is unknown.`)}

  if (!existsSync(selected.python)) {return fail(`Selected environment ${selected.root} has no ${pythonName}.`)}

  const environmentType = options.installationSource !== 'unknown'
    ? options.installationSource
    : options.runtimeMode !== 'unknown'
      ? options.runtimeMode
      : (selected.name === 'venv' ? 'managed' : 'development')

  return { root: selected.root, python: selected.python, launcher: selected.launcher, environmentType, error: null }
}

export function probeUpdateVenv(
  projectRoot: string,
  target: UpdateVenvTarget,
  run: typeof execFileSync = execFileSync,
  requiredModules: readonly string[] = UPDATE_REQUIRED_MODULES
): string | null {
  if (target.error || !target.root || !target.python) {return target.error ?? 'No selected update Python is available.'}

  try {
    const script = [
      'import importlib, json, sys',
      'modules = json.loads(sys.argv[1])',
      "sys.argv = ['stardust', 'update']",
      'for name in modules:',
      '    importlib.import_module(name)',
      "print(json.dumps({'prefix': sys.prefix}))"
    ].join('\n')

    const output = String(run(target.python, ['-c', script, JSON.stringify(requiredModules)], {
      cwd: projectRoot,
      encoding: 'utf8',
      timeout: 15000,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    }))

    const lastLine = output.trim().split(/\r?\n/).filter(Boolean).at(-1)
    const prefix = lastLine ? JSON.parse(lastLine).prefix : null
    const expectedRoot = realpathSync(target.root)
    const actualRoot = prefix ? realpathSync(prefix) : null

    if (!actualRoot || path.normalize(actualRoot).toLowerCase() !== path.normalize(expectedRoot).toLowerCase()) {
      return `Selected Python reports sys.prefix ${String(prefix)}, which does not match ${target.root}.`
    }

    return null
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)

    return `Selected Python is not healthy or cannot import hermes_cli.main: ${message.slice(0, 300)}`
  }
}
