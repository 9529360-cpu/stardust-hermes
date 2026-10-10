import { type ChildProcess, spawn as nodeSpawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export type BridgeStatus = 'stopped' | 'starting' | 'connected' | 'stopping' | 'error'
export type BrowserBridgeStatus = {
  status: 'inactive' | 'starting' | 'connected' | 'stopping' | 'error'
  error?: string
  session_id?: string
  controller_id?: string
  browser_profile_id?: string
  capabilities?: string[]
}
export type BridgeLaunchContext = {
  grant: string
  session_id: string
  controller_id: string
  browser_profile_id: string
  capabilities: string[]
  protocol_version: number
  profile_id?: string
}

type Spawned = Pick<ChildProcess, 'pid' | 'stdout' | 'stderr' | 'once' | 'kill'>
type Deps = {
  spawn?: (command: string, args: string[], options: { env: NodeJS.ProcessEnv; stdio: string[] }) => Spawned
  bridgeEntry?: string
  packagedBridgeEntry?: (resourcesPath: string) => string | null
  resourcesPath?: string
  env?: NodeJS.ProcessEnv
}

const MAX_TEXT = 320
const MAX_GRANT = 4096

const SAFE_ENV = [
  'PATH',
  'Path',
  'HOME',
  'USERPROFILE',
  'APPDATA',
  'LOCALAPPDATA',
  'SYSTEMROOT',
  'TEMP',
  'TMP',
  'TMPDIR'
]

const PROFILE_RE = /^[\w .-]{1,90}$/
const ID_RE = /^[\w:-]{1,128}$/
const PACKAGE_RE = /^@playwright\/mcp@\d+\.\d+\.\d+$/

function bounded(value: unknown, max = MAX_TEXT) {
  return String(value ?? '')
    .replace(/[\r\n]+/g, ' ')
    .replace(/(?:grant|token|secret|authorization|bearer)[^ ]*/gi, '[REDACTED]')
    .slice(0, max)
}

function loopback(raw: string) {
  const url = new URL(raw)

  if (
    url.protocol !== 'http:' ||
    !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error('Only a loopback HTTP gateway root is supported')
  }

  url.pathname = '/'

  return url.toString()
}

function exactObject(value: unknown, keys: string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Invalid bridge launch context')
  }

  const actual = Object.keys(value as object).sort()

  if (actual.join('|') !== keys.slice().sort().join('|')) {
    throw new Error('Bridge launch context contains unsupported fields')
  }
}

export function validateBridgeLaunchContext(raw: unknown): BridgeLaunchContext {
  const keys = [
    'grant',
    'session_id',
    'controller_id',
    'browser_profile_id',
    'capabilities',
    'protocol_version',
    ...(raw && typeof raw === 'object' && 'profile_id' in raw ? ['profile_id'] : [])
  ]

  exactObject(raw, keys)
  const c = raw as Record<string, unknown>

  for (const key of ['session_id', 'controller_id', 'browser_profile_id']) {
    if (typeof c[key] !== 'string' || !ID_RE.test(c[key] as string)) {
      throw new Error(`Invalid ${key}`)
    }
  }

  if (typeof c.grant !== 'string' || !c.grant || c.grant.length > MAX_GRANT || /[\r\n]/.test(c.grant)) {
    throw new Error('Invalid one-time browser grant')
  }

  if (
    !Array.isArray(c.capabilities) ||
    c.capabilities.length === 0 ||
    c.capabilities.length > 32 ||
    c.capabilities.some(x => typeof x !== 'string' || x.length > 80)
  ) {
    throw new Error('Invalid browser capabilities')
  }

  if (c.protocol_version !== 1) {
    throw new Error('Unsupported browser protocol version')
  }

  return {
    grant: c.grant as string,
    session_id: c.session_id as string,
    controller_id: c.controller_id as string,
    browser_profile_id: c.browser_profile_id as string,
    capabilities: c.capabilities as string[],
    protocol_version: 1,
    ...(typeof c.profile_id === 'string' ? { profile_id: c.profile_id } : {})
  }
}

function resolveEntry(deps: Deps) {
  if (deps.bridgeEntry) {
    return deps.bridgeEntry
  }

  if (deps.packagedBridgeEntry) {
    const resolved = deps.packagedBridgeEntry(deps.resourcesPath || '')

    if (resolved) {
      return resolved
    }

    throw new Error('Packaged Playwright bridge resource is unavailable')
  }

  const dev = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../../experiments/playwright-mcp-host-bridge/src/main.mjs'
  )

  if (fs.existsSync(dev)) {
    return dev
  }

  throw new Error('Playwright bridge entry is unavailable; packaging must include the bridge resource')
}

export class BrowserControlBridgeSupervisor {
  private child: Spawned | null = null
  private state: BridgeStatus = 'stopped'
  private error: string | null = null
  private lastContext: BridgeLaunchContext | null = null
  private startupTimer: ReturnType<typeof setTimeout> | null = null
  private readonly deps: Deps
  constructor(deps: Deps = {}) {
    this.deps = { spawn: nodeSpawn as unknown as Deps['spawn'], ...deps }
  }
  status() {
    if (this.state === 'stopped') {
      return { status: 'inactive' as const }
    }

    const context = this.lastContext

    return {
      status:
        this.state === 'error'
          ? ('error' as const)
          : this.state === 'starting'
            ? ('starting' as const)
            : this.state === 'stopping'
              ? ('stopping' as const)
              : ('connected' as const),
      ...(context
        ? {
            session_id: context.session_id,
            controller_id: context.controller_id,
            browser_profile_id: context.browser_profile_id,
            capabilities: context.capabilities
          }
        : {}),
      ...(this.error ? { error: this.error } : {})
    }
  }
  async start(raw: { gatewayUrl: string; launchContext: unknown; chromeProfileDir: string; packageSpec: string }) {
    exactObject(raw, ['gatewayUrl', 'launchContext', 'chromeProfileDir', 'packageSpec'])
    const context = validateBridgeLaunchContext(raw.launchContext)
    const gatewayUrl = loopback(raw.gatewayUrl)

    if (typeof raw.chromeProfileDir !== 'string' || !PROFILE_RE.test(raw.chromeProfileDir)) {
      throw new Error('Invalid Chrome profile directory name')
    }

    if (typeof raw.packageSpec !== 'string' || !PACKAGE_RE.test(raw.packageSpec)) {
      throw new Error('Playwright MCP package must be exact semver')
    }

    if (
      this.child &&
      this.state !== 'error' &&
      this.state !== 'stopped' &&
      this.lastContext?.session_id === context.session_id &&
      this.lastContext.controller_id === context.controller_id &&
      this.lastContext.browser_profile_id === context.browser_profile_id
    ) {
      return this.status()
    }

    await this.stop()

    if (this.child) {
      throw new Error('Previous browser bridge has not exited')
    }

    const entry = resolveEntry(this.deps)
    const source = this.deps.env || process.env
    const env: NodeJS.ProcessEnv = {}

    for (const name of SAFE_ENV) {
      if (source[name]) {
        env[name] = source[name]
      }
    }

    env.ELECTRON_RUN_AS_NODE = '1'
    Object.assign(env, {
      STARDUST_GATEWAY_URL: gatewayUrl,
      STARDUST_SESSION_ID: context.session_id,
      STARDUST_BROWSER_CONTROL_GRANT: context.grant,
      STARDUST_CONTROLLER_ID: context.controller_id,
      STARDUST_BROWSER_PROFILE_ID: context.browser_profile_id,
      STARDUST_BROWSER_CAPABILITIES: JSON.stringify(context.capabilities),
      STARDUST_BROWSER_PROTOCOL_VERSION: String(context.protocol_version),
      STARDUST_CHROME_PROFILE_DIR: raw.chromeProfileDir,
      PLAYWRIGHT_MCP_PACKAGE: raw.packageSpec
    })
    this.state = 'starting'
    this.error = null
    this.lastContext = context

    try {
      const child = this.deps.spawn!(process.execPath, [entry], { env, stdio: ['ignore', 'pipe', 'pipe'] })
      this.child = child
      let buffered = ''

      this.startupTimer = setTimeout(() => {
        if (this.child !== child || this.state !== 'starting') {
          return
        }

        this.state = 'error'
        this.error = 'Browser bridge did not become ready within 15 seconds'
        child.kill('SIGTERM')
      }, 15_000)

      const parseLine = (rawLine: string) => {
        if (this.child !== child) {
          return
        }

        const line = rawLine.trim()

        if (!line) {
          return
        }

        let event: unknown

        try {
          event = JSON.parse(line)
        } catch {
          return
        }

        if (!event || typeof event !== 'object') {
          return
        }

        const kind = (event as { event?: unknown }).event

        if (kind === 'ready' && this.state === 'starting') {
          this.clearStartupTimer()
          this.state = 'connected'
        }

        if (kind === 'stopped') {
          this.state = 'stopped'
          this.error = null
        }

        if (kind === 'error') {
          this.clearStartupTimer()
          this.state = 'error'
          this.error = bounded(
            String((event as { message?: unknown }).message || '').replaceAll(context.grant, '[REDACTED]')
          )
        }
      }

      child.stdout?.on('data', chunk => {
        buffered += String(chunk ?? '')
        const lines = buffered.split(/\r?\n/)
        buffered = lines.pop() || ''

        if (buffered.length > 8192) {
          buffered = ''
        }

        for (const line of lines) {
          parseLine(line)
        }
      })
      let stderrBuffered = ''
      child.stderr?.on('data', chunk => {
        // stderr is a separate stream; never splice it into a partial stdout frame.
        stderrBuffered += String(chunk ?? '')
        const lines = stderrBuffered.split(/\r?\n/)
        stderrBuffered = lines.pop() || ''

        if (stderrBuffered.length > 8192) {
          stderrBuffered = ''
        }

        for (const line of lines) {
          parseLine(line)
        }
      })
      child.once('error', error => {
        if (this.child !== child) {
          return
        }

        this.clearStartupTimer()
        this.state = 'error'
        this.error = bounded(error)

        if (child.pid === undefined) {
          this.child = null
        }
      })
      child.once('exit', (code: number | null) => {
        if (this.child !== child) {
          return
        }

        this.clearStartupTimer()
        this.child = null

        if (this.state !== 'stopping' && this.state !== 'stopped' && this.state !== 'error') {
          this.state = code === 0 ? 'stopped' : 'error'
          this.error = code === 0 ? null : 'Bridge child exited unexpectedly'
        }
      })
    } catch (error) {
      this.state = 'error'
      this.error = bounded(error)
      throw error
    }

    return this.status()
  }
  async stop() {
    const child = this.child
    this.clearStartupTimer()

    if (!child) {
      this.state = 'stopped'

      return this.status()
    }

    this.state = 'stopping'
    await new Promise<void>(resolve => {
      const timer = setTimeout(() => {
        child.kill('SIGKILL')
        finalTimer = setTimeout(resolve, 1500)
      }, 1500)

      let finalTimer: ReturnType<typeof setTimeout> | undefined
      child.once('exit', () => {
        clearTimeout(timer)
        clearTimeout(finalTimer)
        resolve()
      })
      child.kill('SIGTERM')
    })

    if (this.child === child) {
      this.state = 'error'
      this.error = 'Browser bridge did not exit; retry disconnect before pairing again'

      return this.status()
    }

    this.state = 'stopped'
    this.error = null

    return this.status()
  }
  private clearStartupTimer() {
    if (this.startupTimer) {
      clearTimeout(this.startupTimer)
    }

    this.startupTimer = null
  }
  async revoke() {
    return this.stop()
  }
}
