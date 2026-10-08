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
  clock?: () => number
  bridgeEntry?: string
  packagedBridgeEntry?: (resourcesPath: string) => string | null
  resourcesPath?: string
  platform?: NodeJS.Platform
  env?: NodeJS.ProcessEnv
}

const MAX_TEXT = 320
const MAX_GRANT = 4096
const SAFE_ENV = ['PATH', 'Path', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMROOT', 'TEMP', 'TMP', 'TMPDIR']
const PROFILE_RE = /^[\w .-]{1,90}$/
const ID_RE = /^[\w:-]{1,128}$/
const PACKAGE_RE = /^@playwright\/mcp@\d+\.\d+\.\d+$/

function bounded(value: unknown, max = MAX_TEXT) {
  return String(value ?? '').replace(/[\r\n]+/g, ' ').replace(/(?:grant|token|secret|authorization|bearer)[^ ]*/gi, '[REDACTED]').slice(0, max)
}

function loopback(raw: string) {
  const url = new URL(raw)

  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password || url.search || url.hash) {throw new Error('Only a loopback HTTP gateway root is supported')}
  url.pathname = '/'

  return url.toString()
}

function exactObject(value: unknown, keys: string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error('Invalid bridge launch context')}
  const actual = Object.keys(value as object).sort()

  if (actual.join('|') !== keys.slice().sort().join('|')) {throw new Error('Bridge launch context contains unsupported fields')}
}

export function validateBridgeLaunchContext(raw: unknown): BridgeLaunchContext {
  const keys = ['grant', 'session_id', 'controller_id', 'browser_profile_id', 'capabilities', 'protocol_version', ...(raw && typeof raw === 'object' && 'profile_id' in raw ? ['profile_id'] : [])]
  exactObject(raw, keys)
  const c = raw as Record<string, unknown>

  for (const key of ['session_id', 'controller_id', 'browser_profile_id']) {if (typeof c[key] !== 'string' || !ID_RE.test(c[key] as string)) {throw new Error(`Invalid ${key}`)}}

  if (typeof c.grant !== 'string' || !c.grant || c.grant.length > MAX_GRANT || /[\r\n]/.test(c.grant)) {throw new Error('Invalid one-time browser grant')}

  if (!Array.isArray(c.capabilities) || c.capabilities.length === 0 || c.capabilities.length > 32 || c.capabilities.some(x => typeof x !== 'string' || x.length > 80)) {throw new Error('Invalid browser capabilities')}

  if (c.protocol_version !== 1) {throw new Error('Unsupported browser protocol version')}

  return { grant: c.grant as string, session_id: c.session_id as string, controller_id: c.controller_id as string, browser_profile_id: c.browser_profile_id as string, capabilities: c.capabilities as string[], protocol_version: 1, ...(typeof c.profile_id === 'string' ? { profile_id: c.profile_id } : {}) }
}

function resolveEntry(deps: Deps) {
  if (deps.bridgeEntry) {return deps.bridgeEntry}

  if (deps.packagedBridgeEntry) {
    const resolved = deps.packagedBridgeEntry(deps.resourcesPath || '')

    if (resolved) {return resolved}
    throw new Error('Packaged Playwright bridge resource is unavailable')
  }

  const dev = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../experiments/playwright-mcp-host-bridge/src/main.mjs')

  if (fs.existsSync(dev)) {return dev}
  throw new Error('Playwright bridge entry is unavailable; packaging must include the bridge resource')
}

export class BrowserControlBridgeSupervisor {
  private child: Spawned | null = null
  private state: BridgeStatus = 'stopped'
  private error: string | null = null
  private lastContext: BridgeLaunchContext | null = null
  private readonly deps: Required<Pick<Deps, 'spawn' | 'clock'>> & Deps
  constructor(deps: Deps = {}) { this.deps = { spawn: nodeSpawn as unknown as Deps['spawn'], clock: Date.now, ...deps } as any }
  status() {
    if (this.state === 'stopped') {return { status: 'inactive' as const }}
    const context = this.lastContext

    return { status: this.state === 'error' ? 'error' as const : this.state === 'starting' ? 'starting' as const : this.state === 'stopping' ? 'stopping' as const : 'connected' as const, ...(context ? { session_id: context.session_id, controller_id: context.controller_id, browser_profile_id: context.browser_profile_id, capabilities: context.capabilities } : {}), ...(this.error ? { error: this.error } : {}) }
  }
  async start(raw: { gatewayUrl: string; launchContext: unknown; chromeProfileDir: string; packageSpec: string }) {
    exactObject(raw, ['gatewayUrl', 'launchContext', 'chromeProfileDir', 'packageSpec'])
    const context = validateBridgeLaunchContext(raw.launchContext)
    this.lastContext = context
    const gatewayUrl = loopback(raw.gatewayUrl)

    if (typeof raw.chromeProfileDir !== 'string' || !PROFILE_RE.test(raw.chromeProfileDir)) {throw new Error('Invalid Chrome profile directory name')}

    if (typeof raw.packageSpec !== 'string' || !PACKAGE_RE.test(raw.packageSpec)) {throw new Error('Playwright MCP package must be exact semver')}

    if (this.child && this.state !== 'error' && this.state !== 'stopped') {return this.status()}
    await this.stop()
    const entry = resolveEntry(this.deps)
    const source = this.deps.env || process.env
    const env: NodeJS.ProcessEnv = {}

    for (const name of SAFE_ENV) {if (source[name]) {env[name] = source[name]}}
    Object.assign(env, { STARDUST_GATEWAY_URL: gatewayUrl, STARDUST_SESSION_ID: context.session_id, STARDUST_BROWSER_CONTROL_GRANT: context.grant, STARDUST_CONTROLLER_ID: context.controller_id, STARDUST_BROWSER_PROFILE_ID: context.browser_profile_id, STARDUST_BROWSER_CAPABILITIES: JSON.stringify(context.capabilities), STARDUST_BROWSER_PROTOCOL_VERSION: String(context.protocol_version), STARDUST_CHROME_PROFILE_DIR: raw.chromeProfileDir, PLAYWRIGHT_MCP_PACKAGE: raw.packageSpec })
    this.state = 'starting'; this.error = null

    try {
      const child = this.deps.spawn!(process.execPath, [entry], { env, stdio: ['ignore', 'pipe', 'pipe'] })
      this.child = child

      const parseEvent = (chunk: unknown) => {
        for (const rawLine of String(chunk ?? '').split(/\r?\n/)) {
          const line = rawLine.trim()

          if (!line) {continue}
          let event: unknown

          try { event = JSON.parse(line) } catch { continue }

          if (!event || typeof event !== 'object') {continue}
          const kind = (event as { event?: unknown }).event

          if (kind === 'ready') {this.state = 'connected'}

          if (kind === 'stopped') {
            this.state = 'stopped'
            this.error = null
          }

          if (kind === 'error') {
            this.state = 'error'
            this.error = bounded((event as { message?: unknown }).message)
          }
        }
      }

      child.stdout?.on('data', parseEvent)
      child.stderr?.on('data', parseEvent)

      if (child.stdout === null && child.stderr === null) {this.state = 'connected'}
      child.once('exit', (code: number | null) => { this.child = null;

 if (this.state !== 'stopping' && this.state !== 'stopped') { this.state = code === 0 ? 'stopped' : 'error'; this.error = code === 0 ? null : 'Bridge child exited unexpectedly' } })
    } catch (error) { this.state = 'error'; this.error = bounded(error); throw error }

    return this.status()
  }
  async stop() {
    const child = this.child

    if (!child) { this.state = 'stopped';

 return this.status() }

    this.state = 'stopping';
    child.kill?.('SIGTERM');
    await new Promise<void>(resolve => { const timer = setTimeout(resolve, 1500); child.once('exit', () => { clearTimeout(timer); resolve() }) })

    if (this.child === child) {this.child = null}
    this.state = 'stopped'; this.error = null

    return this.status()
  }
  async revoke() { return this.stop() }
}
