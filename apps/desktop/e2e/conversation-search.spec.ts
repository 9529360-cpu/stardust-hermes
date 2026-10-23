import { type ChildProcess, spawn, spawnSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import * as fs from 'node:fs'
import type * as NodeHttp from 'node:http'
import * as path from 'node:path'

import { startMockServer } from '../../../tests-js/scripts/mock-server'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  type Sandbox,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { allowErrorBanners, collectErrorBanners, type ElectronApplication, expect, type Page, test } from './test'

const REPO_ROOT = path.resolve(import.meta.dirname, '../../..')
const SESSION_A = 'acceptance-search-trip'
const SESSION_B = 'acceptance-search-menu'
const TITLE_A = '周末安排记录'
const TITLE_B = '晚餐准备记录'
const NEEDLE_A = '海风灯塔'
const NEEDLE_B = '桂花汤圆'
const MESSAGE_A = `之前讨论的${NEEDLE_A}路线已经确认，周六上午出发。`
const MESSAGE_B = `晚餐准备${NEEDLE_B}，记得提前购买食材。`
const FOLLOW_UP = '继续之前的计划，请只确认这条消息已收到。'
const REPLY = '已经收到，原来的计划仍然保留。'
const PERSONAL_PROFILE = 'search-personal'
const PERSONAL_SESSION = 'acceptance-search-personal'
const PERSONAL_NEEDLE = '私人书架青鸟'
const SHARED_SESSION = 'acceptance-search-shared'
const SHARED_DEFAULT_TITLE = '默认档同号会话'
const SHARED_PERSONAL_TITLE = '个人档同号会话'
const SHARED_DEFAULT_NEEDLE = '晨光茶杯'
const SHARED_PERSONAL_NEEDLE = '暮色纸鹤'
const SHARED_DEFAULT_BODY = `默认档的${SHARED_DEFAULT_NEEDLE}记录只属于默认配置。`
const SHARED_PERSONAL_BODY = `个人档的${SHARED_PERSONAL_NEEDLE}记录只属于个人配置。`
const LEGACY_PERSONAL_FOLLOW_UP = '只属于远程个人同号会话的继续消息，请确认收到。'

interface RegistryCommandSnapshot {
  key: string
  status: number | null
  stdout: string
  stderr: string
}

interface ProtocolRegistrySnapshot {
  platform: string
  commands: RegistryCommandSnapshot[]
}

interface Fixture {
  app: ElectronApplication
  page: Page
  sandbox: Sandbox
  mock: Awaited<ReturnType<typeof startMockServer>>
  mockUrl: string
  registryBefore: ProtocolRegistrySnapshot | null
  ownedProcesses: ChildProcess[]
  ownedServeProcesses: ChildProcess[]
  backendPids: number[]
  remoteHome?: string
  remoteOrigin?: string
  remoteHttpStatus?: number
  remoteMessages?: (id: string, profile: string) => Promise<{ messages: Array<{ content?: unknown }> }>
  remoteSockets?: Array<{ origin: string; pathname: string; profile: string | null; receivedFrames: number }>
  cleanup: () => Promise<void>
}

interface DesktopWindow extends Window {
  stardustDesktop: {
    api: <T>(request: { path: string; profile?: string }) => Promise<T>
    quickEntry: { getSettings: () => Promise<{ enabled: boolean; error: null | string; registered: boolean; shortcut: string }> }
    getConnection: (profile?: string) => Promise<{ baseUrl: string; mode?: string; source?: string; connectionId?: string; profile?: string }>
  }
}

const PROTOCOL_COMMAND_KEYS = [
  'HKCU\\Software\\Classes\\stardust\\shell\\open\\command',
  'HKCU\\Software\\Classes\\hermes\\shell\\open\\command'
] as const

function snapshotProtocolRegistry(): ProtocolRegistrySnapshot | null {
  if (process.platform !== 'win32') {return null}

  return {
    platform: process.platform,
    commands: PROTOCOL_COMMAND_KEYS.map(key => {
      const result = spawnSync('reg.exe', ['query', key, '/ve'], {
        encoding: 'utf8',
        timeout: 5_000,
        windowsHide: true
      })

      if (result.error) {throw new Error(`Could not read HKCU protocol metadata for ${key}: ${result.error.message}`)}

      if (result.status !== 0 && result.status !== 1) {
        throw new Error(`Unexpected reg.exe query status ${result.status} for ${key}`)
      }

      return { key, status: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' }
    })
  }
}

function isolatedEnv(sandbox: Sandbox) {
  const env = buildAppEnv(sandbox, {
    HERMES_HOME: sandbox.hermesHome,
    HERMES_SHARED_AUTH_DIR: path.join(sandbox.hermesHome, 'shared'),
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONUTF8: '1'
  })

  for (const key of ['NODE_ENV', 'ELECTRON_RUN_AS_NODE', 'STARDUST_DESKTOP_DEV_SERVER', 'STARDUST_DESKTOP_BOOT_FAKE', 'STARDUST_DESKTOP_BOOT_FAKE_ERROR', 'STARDUST_DESKTOP_REMOTE_URL', 'STARDUST_DESKTOP_REMOTE_TOKEN']) {
    delete env[key]
  }

  return env
}

// Seed only this test's database. Restoring, searching and continuing these rows
// still go through the native app and its real local Python backend.
function seedHistory(sandbox: Sandbox): void {
  const python = process.env.STARDUST_DESKTOP_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')

  const result = spawnSync(python, ['-B', '-c', `
import json, pathlib, sqlite3, sys, time
from hermes_state import SessionDB
home = pathlib.Path(sys.argv[1])
rows = json.loads(sys.argv[2])
now = time.time()
db = SessionDB(home / 'state.db')
try:
    for row in rows:
        db.create_session(row['id'], source='desktop', model='mock-model')
        db.set_session_title(row['id'], row['title'])
        db.append_message(row['id'], role='user', content='先保留这份讨论记录。', timestamp=now-86400)
        db.append_message(row['id'], role='assistant', content=row['message'], timestamp=now-86399)
    # More than the REST page cap (100), so the target rows cannot be
    # found merely by searching the initially loaded title/preview cache.
    for i in range(105):
        sid = f'acceptance-search-recent-{i}'
        db.create_session(sid, source='desktop', model='mock-model')
        db.set_session_title(sid, f'近期无关记录 {i}')
        db.append_message(sid, role='user', content=f'无关的近期内容 {i}', timestamp=now-200+i)
finally:
    db.close()
with sqlite3.connect(home / 'state.db') as conn:
    conn.executemany('UPDATE sessions SET started_at=? WHERE id=?', [(now-86400, row['id']) for row in rows])
personal = home / 'profiles' / '${PERSONAL_PROFILE}'
personal.mkdir(parents=True, exist_ok=True)
db = SessionDB(personal / 'state.db')
try:
    db.create_session('${PERSONAL_SESSION}', source='desktop', model='mock-model')
    db.set_session_title('${PERSONAL_SESSION}', '个人阅读记录')
    db.append_message('${PERSONAL_SESSION}', role='user', content='${PERSONAL_NEEDLE}')
    db.create_session('${SHARED_SESSION}', source='desktop', model='mock-model')
    db.set_session_title('${SHARED_SESSION}', '${SHARED_PERSONAL_TITLE}')
    db.append_message('${SHARED_SESSION}', role='user', content='${SHARED_PERSONAL_BODY}')
finally:
    db.close()
`, sandbox.hermesHome, JSON.stringify([
    { id: SESSION_A, title: TITLE_A, message: MESSAGE_A },
    { id: SESSION_B, title: TITLE_B, message: MESSAGE_B },
    { id: SHARED_SESSION, title: SHARED_DEFAULT_TITLE, message: SHARED_DEFAULT_BODY }
  ])], {
    cwd: REPO_ROOT,
    env: { ...isolatedEnv(sandbox), PYTHONPATH: REPO_ROOT },
    encoding: 'utf8',
    timeout: 30_000
  })

  if (result.error || result.status !== 0) {
    throw new Error(`isolated history seed failed: ${result.error?.message ?? result.stderr}`)
  }
}

async function setup(): Promise<Fixture> {
  const registryBefore = snapshotProtocolRegistry()
  const mock = await startMockServer({ replyForPrompt: () => REPLY })
  const sandbox = createSandbox('conversation-search')
  let app: ElectronApplication | undefined

  try {
    writeMockProviderConfig(sandbox.hermesHome, mock.url, '  language: zh')
    writeEnvFile(sandbox.hermesHome)
    seedHistory(sandbox)
    const personalHome = path.join(sandbox.hermesHome, 'profiles', PERSONAL_PROFILE)
    writeMockProviderConfig(personalHome, mock.url, '  language: zh')
    writeEnvFile(personalHome)
    const launched = await launchDesktop(isolatedEnv(sandbox))
    app = launched.app

    const fixture: Fixture = {
      app,
      page: launched.page,
      mock,
      mockUrl: mock.url,
      registryBefore,
      ownedProcesses: [app.process()],
      ownedServeProcesses: [],
      backendPids: [],
      sandbox,
      cleanup: async () => {
        try { await fixture.app.close() } finally {
          try { await mock.close() } finally { sandbox.cleanup() }
        }
      }
    }

    await waitForAppReady(fixture, 120_000)

    return fixture
  } catch (error) {
    try { await app?.close() } finally {
      await mock.close()
      sandbox.cleanup()
    }

    throw error
  }
}

async function stopOwnedServe(child: ChildProcess): Promise<void> {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) {return}
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Owned serve PID ${child.pid} did not exit`)), 15_000)
    child.once('exit', () => { clearTimeout(timer); resolve() })

    try {
      if (process.platform === 'win32') {
        const taskkill = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe')
        const result = spawnSync(taskkill, ['/PID', String(child.pid), '/T', '/F'], { encoding: 'utf8', windowsHide: true, timeout: 10_000 })

        if (result.error || (result.status !== 0 && processIsAlive(child.pid!))) {
          throw new Error(`Could not stop owned serve PID ${child.pid}: ${result.error?.message ?? result.stderr}`)
        }
      } else {
        child.kill('SIGTERM')
      }
    } catch (error) { clearTimeout(timer); reject(error) }
  })
}

async function startLegacyRemoteServe(sandbox: Sandbox, token: string) {
  const python = process.env.STARDUST_DESKTOP_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
  const env: Record<string, string> = { ...isolatedEnv(sandbox), PYTHONPATH: REPO_ROOT, STARDUST_DASHBOARD_SESSION_TOKEN: token }
  delete env.STARDUST_DESKTOP
  delete env.STARDUST_PARENT_PID

  const child = spawn(python, ['-B', '-m', 'hermes_cli.main', 'serve', '--host', '127.0.0.1', '--port', '0', '--skip-build'], {
    cwd: REPO_ROOT, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true
  })

  let tail = ''

  try {
    // CLI parser accepts --port 0 and --skip-build. web_server._on_server_started
    // emits this sentinel to fd 1 after binding, using the actual socket port.
    const port = await new Promise<number>((resolve, reject) => {
      let done = false
      const timer = setTimeout(() => finish(new Error(`Real serve readiness timed out: ${tail}`)), 90_000)

      const finish = (error?: Error, readyPort?: number) => {
        if (done) {return}
        done = true
        clearTimeout(timer)

        if (error) {reject(error)}
        else {resolve(readyPort!)}
      }

      child.stdout?.on('data', chunk => {
        tail = (tail + String(chunk).replaceAll(token, '[test-token]')).slice(-8192)
        const match = /STARDUST_BACKEND_READY port=(\d+)/.exec(tail)

        if (match && Number(match[1]) > 0 && Number(match[1]) < 65536) {finish(undefined, Number(match[1]))}
      })
      child.stderr?.on('data', chunk => { tail = (tail + String(chunk).replaceAll(token, '[test-token]')).slice(-8192) })
      child.once('error', error => finish(new Error(`Real serve launch failed: ${error.message}`)))
      child.once('exit', code => finish(new Error(`Real serve exited before readiness (${code}): ${tail}`)))
    })

    const origin = `http://127.0.0.1:${port}`

    const response = await fetch(`${origin}/api/status`, {
      headers: { 'X-Hermes-Session-Token': token }, signal: AbortSignal.timeout(10_000)
    })

    if (!response.ok) {throw new Error(`Real serve HTTP readiness failed (${response.status})`)}

    return { child, origin, httpStatus: response.status }
  } catch (error) {
    await stopOwnedServe(child)
    throw error
  }
}

async function setupLegacyRemote(): Promise<Fixture> {
  const registryBefore = snapshotProtocolRegistry()
  const mock = await startMockServer({ replyForPrompt: () => REPLY })
  const sandbox = createSandbox('legacy-env-remote')
  const remoteHome = path.join(sandbox.root, 'remote-home')
  const remoteSandbox = { ...sandbox, hermesHome: remoteHome }
  let remote: Awaited<ReturnType<typeof startLegacyRemoteServe>> | undefined
  let app: ElectronApplication | undefined

  try {
    fs.mkdirSync(remoteHome, { recursive: true })
    writeMockProviderConfig(remoteHome, mock.url, '  language: zh')
    writeEnvFile(remoteHome)
    seedHistory(remoteSandbox)
    const personalHome = path.join(remoteHome, 'profiles', PERSONAL_PROFILE)
    writeMockProviderConfig(personalHome, mock.url, '  language: zh')
    writeEnvFile(personalHome)
    fs.writeFileSync(path.join(sandbox.hermesHome, 'config.yaml'), '# Separate empty app home; all session data belongs to the real remote backend.\n')
    expect(fs.existsSync(path.join(sandbox.hermesHome, 'state.db'))).toBe(false)
    expect(fs.existsSync(path.join(sandbox.userDataDir, 'connections.json'))).toBe(false)
    const token = randomBytes(24).toString('hex')
    remote = await startLegacyRemoteServe(remoteSandbox, token)

    const env = {
      ...isolatedEnv(sandbox),
      STARDUST_DESKTOP_REMOTE_URL: remote.origin,
      STARDUST_DESKTOP_REMOTE_TOKEN: token
    }

    const launched = await launchDesktop(env)
    app = launched.app
    const remoteSockets: NonNullable<Fixture['remoteSockets']> = []

    // firstWindow() can resolve after the initial socket has already opened.
    // Observe actual RPC sends, including on that existing socket, without
    // changing their payload or delivery. A late page 'websocket' listener
    // only sees newly-created sockets and can miss the whole live channel.
    await launched.page.evaluate(() => {
      const observed: Array<{ origin: string; pathname: string; profile: string | null; receivedFrames: number }> = []
      const seen = new WeakSet<WebSocket>()
      const send = WebSocket.prototype.send
      Object.assign(window, { __searchAcceptanceSockets: observed })
      WebSocket.prototype.send = function (data) {
        if (!seen.has(this)) {
          seen.add(this)
          const url = new URL(this.url)
          const socket = { origin: url.origin, pathname: url.pathname, profile: url.searchParams.get('profile'), receivedFrames: 0 }
          observed.push(socket)
          this.addEventListener('message', () => { socket.receivedFrames += 1 })
        }

        return send.call(this, data)
      }
    })
    const ownedRemote = remote

    const fixture: Fixture = {
      app, page: launched.page, mock, mockUrl: mock.url, sandbox, registryBefore,
      ownedProcesses: [app.process()], ownedServeProcesses: [ownedRemote.child],
      backendPids: [ownedRemote.child.pid!], remoteHome, remoteOrigin: ownedRemote.origin, remoteHttpStatus: ownedRemote.httpStatus, remoteSockets,
      remoteMessages: async (id, profile) => {
        const response = await fetch(`${ownedRemote.origin}/api/sessions/${encodeURIComponent(id)}/messages?profile=${encodeURIComponent(profile)}`, {
          headers: { 'X-Hermes-Session-Token': token }, signal: AbortSignal.timeout(15_000)
        })

        if (!response.ok) {throw new Error(`Owned remote messages HTTP status ${response.status}`)}

        return response.json() as Promise<{ messages: Array<{ content?: unknown }> }>
      },
      cleanup: async () => {
        try { await fixture.app.close() } finally {
          try { await stopOwnedServe(ownedRemote.child) } finally {
            try { await mock.close() } finally { sandbox.cleanup() }
          }
        }
      }
    }

    await waitForAppReady(fixture, 120_000)

    return fixture
  } catch (error) {
    try { await app?.close() } finally {
      try { if (remote) {await stopOwnedServe(remote.child)} } finally {
        try { await mock.close() } finally { sandbox.cleanup() }
      }
    }

    throw error
  }
}

function appHomeContainsText(sandbox: Sandbox, text: string): boolean {
  const python = process.env.STARDUST_DESKTOP_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')

  const result = spawnSync(python, ['-B', '-c', `
import pathlib, sqlite3, sys
p = pathlib.Path(sys.argv[1]) / 'state.db'
if not p.exists():
    print('false')
else:
    conn = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)
    try:
        count = conn.execute("SELECT COUNT(*) FROM messages WHERE instr(COALESCE(content, ''), ?) > 0", (sys.argv[2],)).fetchone()[0]
        print('true' if count else 'false')
    finally:
        conn.close()
`, sandbox.hermesHome, text], { cwd: REPO_ROOT, env: isolatedEnv(sandbox), encoding: 'utf8', timeout: 10_000 })

  if (result.error || result.status !== 0) {throw new Error(`Read-only app-home probe failed: ${result.error?.message ?? result.stderr}`)}

  if (!['true', 'false'].includes(result.stdout.trim())) {throw new Error('Read-only app-home probe returned no boolean')}

  return result.stdout.trim() === 'true'
}

async function connectionMetadata(page: Page, profile?: string) {
  return page.evaluate(async requestedProfile => {
    const connection = await (window as unknown as DesktopWindow).stardustDesktop.getConnection(requestedProfile)

    return {
      origin: new URL(connection.baseUrl).origin,
      mode: connection.mode,
      source: connection.source,
      connectionId: connection.connectionId ?? null,
      requestedProfile: requestedProfile ?? 'default',
      responseProfile: connection.profile ?? null
    }
  }, profile)
}

async function openSearch(page: Page) {
  await page.evaluate(() => { window.location.hash = '/command-center?section=sessions' })
  const input = page.getByRole('textbox', { name: '搜索对话', exact: true })
  await expect(input).toBeFocused()

  return input
}

function resultRow(page: Page, id: string) {
  return page.locator(`[data-session-search-result="${id}"]`)
}

function transcript(page: Page) {
  return page.locator('[data-chat-surface]').filter({ visible: true }).last().locator('[data-slot="aui_thread-viewport"]')
}

async function searchApi(page: Page, query: string, profile?: string) {
  return page.evaluate(({ q, profile }) => (window as unknown as DesktopWindow).stardustDesktop.api<{ results: Array<{ session_id: string; snippet?: string }> }>({
    ...(profile ? { profile } : {}),
    path: `/api/sessions/search?q=${encodeURIComponent(q)}`
  }), { q: query, profile })
}

async function messagesApi(page: Page, id: string) {
  return page.evaluate(sid => (window as unknown as DesktopWindow).stardustDesktop.api<{ messages: Array<{ content?: unknown }> }>({
    path: `/api/sessions/${encodeURIComponent(sid)}/messages`
  }), id)
}

async function resize(app: ElectronApplication, width: number): Promise<void> {
  await app.evaluate(({ BrowserWindow }, requestedWidth) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.setContentSize(requestedWidth, 800)
  }, width)
}

function rememberBackendPids(fixture: Fixture): void {
  const ownershipPath = path.join(fixture.sandbox.userDataDir, 'backend-ownership.json')

  if (!fs.existsSync(ownershipPath)) {return}
  const ownership = JSON.parse(fs.readFileSync(ownershipPath, 'utf8')) as { backends?: Array<{ pid: number }> }

  for (const { pid } of ownership.backends ?? []) {
    if (Number.isInteger(pid) && pid > 0 && !fixture.backendPids.includes(pid)) {fixture.backendPids.push(pid)}
  }
}

function processIsAlive(pid: number): boolean {
  try { process.kill(pid, 0);

 return true } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ESRCH') {return false}
    throw error
  }
}

// Supplemental fault cases only: normal search tests never install this hook.
// Redirecting one query to a missing local API yields a real HTTP 404; holding
// another query's real response lets the renderer receive it after newer intent.
async function installSearchFault(app: ElectronApplication, query: string, mode: 'fail' | 'hold') {
  await app.evaluate((_electron, { query, mode }) => {
    const getBuiltinModule = process.getBuiltinModule

    if (typeof getBuiltinModule !== 'function') {
      throw new Error(`Node ${process.versions.node} does not expose process.getBuiltinModule`)
    }

    const http = getBuiltinModule('node:http') as typeof NodeHttp
    const nodeModule = getBuiltinModule('node:module')
    const syncBuiltinESMExports = nodeModule.syncBuiltinESMExports

    const state = globalThis as typeof globalThis & {
      __conversationSearchFault?: { held: boolean; release?: () => void; restore: () => void }
    }

    state.__conversationSearchFault?.restore()
    const original = http.request

    const fault = {
      held: false,
      release: undefined as (() => void) | undefined,
      restore: () => {
        fault.release?.()
        http.request = original
        syncBuiltinESMExports()
        delete state.__conversationSearchFault
      }
    }

    state.__conversationSearchFault = fault
    http.request = ((...args: unknown[]) => {
      const url = args[0]

      if (url instanceof URL && url.pathname === '/api/sessions/search' && url.searchParams.get('q') === query) {
        if (mode === 'fail') {
          const missing = new URL(url)
          missing.pathname = '/api/acceptance-missing-search-endpoint'
          args[0] = missing
        } else {
          const callback = args[2] as (response: NodeHttp.IncomingMessage) => void

          args[2] = (response: NodeHttp.IncomingMessage) => {
            response.pause()
            fault.held = true

            fault.release = () => {
              fault.release = undefined
              callback(response)
              response.resume()
              fault.held = false
            }
          }
        }
      }

      return Reflect.apply(original, http, args)
    }) as typeof original
    syncBuiltinESMExports()
  }, { query, mode })
}

async function releaseSearchFault(app: ElectronApplication): Promise<void> {
  await app.evaluate(() => {
    const state = globalThis as typeof globalThis & { __conversationSearchFault?: { restore: () => void } }
    state.__conversationSearchFault?.restore()
  })
}

async function waitForHeldSearch(app: ElectronApplication): Promise<void> {
  await expect.poll(() => app.evaluate(() => {
    const state = globalThis as typeof globalThis & { __conversationSearchFault?: { held: boolean } }

    return state.__conversationSearchFault?.held ?? false
  }), { timeout: 15_000 }).toBe(true)
}

// Both normal-path tests use the real REST/IPC/SessionDB path. Only inference
// uses a loopback stand-in; no live provider credentials or personal data.
test.describe('native conversation full-text search', () => {
  test.setTimeout(600_000)
  let fixture: Fixture | undefined

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
  test.afterEach(async ({}, testInfo) => {
    if (!fixture) {return}
    const current = fixture

    try {
      if (testInfo.status !== testInfo.expectedStatus) {
        await current.page.screenshot({ path: testInfo.outputPath('native-failure.png') }).catch(() => undefined)
      }

      try { rememberBackendPids(current) } finally { await current.cleanup() }
      await expect.poll(() => current.backendPids.filter(processIsAlive), { timeout: 15_000 }).toEqual([])
    } finally {
      const registryAfter = snapshotProtocolRegistry()
      await testInfo.attach('protocol-command-registry', {
        body: JSON.stringify({ before: current.registryBefore, after: registryAfter, unchanged: JSON.stringify(current.registryBefore) === JSON.stringify(registryAfter) }),
        contentType: 'application/json'
      })

      const cleanup = {
        sandbox: current.sandbox.root,
        sandboxExists: fs.existsSync(current.sandbox.root),
        backendPids: current.backendPids.map(pid => ({ pid, alive: processIsAlive(pid) })),
        serveProcesses: current.ownedServeProcesses.map(child => ({ pid: child.pid, exitCode: child.exitCode, signalCode: child.signalCode })),
        processes: current.ownedProcesses.map(child => ({ pid: child.pid, exitCode: child.exitCode, signalCode: child.signalCode }))
      }

      await testInfo.attach('native-cleanup', { body: JSON.stringify(cleanup), contentType: 'application/json' })
      fixture = undefined
      expect(JSON.stringify(current.registryBefore) === JSON.stringify(registryAfter), 'isolated native launch must not change the two HKCU protocol command defaults').toBe(true)
      expect(cleanup.sandboxExists, 'the owned sandbox must be removed').toBe(false)

      for (const child of cleanup.processes) {
        expect(child.exitCode !== null || child.signalCode !== null, `owned Electron pid ${child.pid} must have exited`).toBe(true)
      }

      for (const child of cleanup.serveProcesses) {
        expect(child.exitCode !== null || child.signalCode !== null, `owned serve pid ${child.pid} must have exited`).toBe(true)
      }
    }
  })

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
  test('opens Chinese body matches, continues the right history and survives restart', async ({}, testInfo) => {
    fixture = await setup()
    let { page } = fixture
    const pageErrors: string[] = []
    page.on('pageerror', error => pageErrors.push(error.message))

    const identity = await fixture.app.evaluate(({ app }) => ({
      appPath: app.getAppPath(),
      userData: app.getPath('userData'),
      home: process.env.STARDUST_HOME,
      legacyHome: process.env.HERMES_HOME,
      isolated: process.env.STARDUST_DESKTOP_ISOLATED,
      backendRoot: process.env.STARDUST_DESKTOP_HERMES_ROOT,
      python: process.env.STARDUST_DESKTOP_PYTHON
    }))

    expect(path.resolve(identity.appPath)).toBe(path.join(REPO_ROOT, 'apps', 'desktop'))
    expect(path.resolve(identity.userData)).toBe(path.resolve(fixture.sandbox.userDataDir))
    expect(identity.home).toBe(fixture.sandbox.hermesHome)
    expect(identity.legacyHome).toBe(fixture.sandbox.hermesHome)
    expect(identity.isolated).toBe('1')
    expect(path.resolve(identity.backendRoot || '')).toBe(REPO_ROOT)
    const quickEntry = await page.evaluate(() => (window as unknown as DesktopWindow).stardustDesktop.quickEntry.getSettings())
    expect(quickEntry).toEqual({
      enabled: false,
      error: null,
      registered: false,
      shortcut: 'CommandOrControl+Shift+Space'
    })

    const globalShortcuts = await fixture.app.evaluate(({ globalShortcut }) => ({
      quickEntryDefault: globalShortcut.isRegistered('CommandOrControl+Shift+Space')
    }))

    expect(globalShortcuts.quickEntryDefault).toBe(false)
    await testInfo.attach('native-isolation', {
      body: JSON.stringify({ ...identity, globalShortcuts }),
      contentType: 'application/json'
    })

    // A direct native bridge query proves the normal service path before any
    // DOM interaction. The keyword occurs only in an old message, not a title.
    const api = await searchApi(page, NEEDLE_A)
    expect(api.results.map(hit => hit.session_id)).toEqual([SESSION_A])
    expect(api.results[0].snippet?.replaceAll('>>>', '').replaceAll('<<<', '')).toContain(NEEDLE_A)
    expect((await searchApi(page, PERSONAL_NEEDLE)).results).toEqual([])
    expect((await searchApi(page, PERSONAL_NEEDLE, PERSONAL_PROFILE)).results.map(hit => hit.session_id)).toEqual([PERSONAL_SESSION])
    expect((await searchApi(page, NEEDLE_A, PERSONAL_PROFILE)).results).toEqual([])

    let input = await openSearch(page)
    // Target history is older than all 105 unrelated recent rows.
    await expect(resultRow(page, SESSION_A)).toHaveCount(0)
    await expect(resultRow(page, SESSION_B)).toHaveCount(0)
    expect((await searchApi(page, TITLE_A)).results.map(hit => hit.session_id)).toEqual([SESSION_A])
    await input.fill(TITLE_A)
    await expect(resultRow(page, SESSION_A)).toContainText(TITLE_A)
    await input.fill(NEEDLE_A)
    const row = resultRow(page, SESSION_A)
    await expect(row).toContainText(TITLE_A)
    await expect(row.locator('[data-session-search-snippet]')).toContainText(NEEDLE_A)
    await expect(resultRow(page, SESSION_B)).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('chinese-body-search-wide.png') })

    await resize(fixture.app, 760)
    await expect(row.locator('[data-session-search-snippet]')).toBeVisible()
    const box = await row.boundingBox()
    const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }))
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1)
    await page.screenshot({ path: testInfo.outputPath('chinese-body-search-narrow.png') })
    await resize(fixture.app, 1220)
    await row.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(MESSAGE_A)
    await expect(transcript(page)).not.toContainText(MESSAGE_B)

    const composer = page.locator('[contenteditable="true"]').filter({ visible: true }).last()
    await composer.click()
    await composer.pressSequentially(FOLLOW_UP, { delay: 5 })
    await page.keyboard.press('Enter')
    await expect(transcript(page)).toContainText(REPLY, { timeout: 60_000 })
    await expect.poll(async () => JSON.stringify(await messagesApi(page, SESSION_A))).toContain(FOLLOW_UP)
    expect(JSON.stringify(await messagesApi(page, SESSION_B))).not.toContain(FOLLOW_UP)

    // Switch via the same search entry, then return. Hidden keep-alive surfaces
    // must not satisfy foreground transcript assertions.
    input = await openSearch(page)
    await input.fill(NEEDLE_B)
    await resultRow(page, SESSION_B).getByRole('button').first().click()
    await expect(transcript(page)).toContainText(MESSAGE_B)
    await expect(transcript(page)).not.toContainText(FOLLOW_UP)
    expect(await collectErrorBanners(page)).toEqual([])
    expect(pageErrors).toEqual([])

    // Exercise profileScoped() through the actual profile rail and search UI,
    // not only through an explicitly scoped bridge call.
    const rail = page.locator('[data-slot="profile-rail"]')
    const personalButton = rail.getByRole('button', { name: PERSONAL_PROFILE, exact: true })
    await expect(personalButton).toBeVisible({ timeout: 60_000 })
    await personalButton.click()
    await expect(personalButton).toHaveAttribute('aria-pressed', 'true', { timeout: 60_000 })
    input = await openSearch(page)
    await input.fill(PERSONAL_NEEDLE)
    const personalRow = resultRow(page, PERSONAL_SESSION)
    await expect(personalRow.locator('[data-session-search-snippet]')).toContainText(PERSONAL_NEEDLE)
    await expect(resultRow(page, SESSION_A)).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('named-profile-body-search.png') })
    await personalRow.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(PERSONAL_NEEDLE)
    await expect(transcript(page)).not.toContainText(MESSAGE_A)

    const defaultButton = rail.getByRole('button', { name: /^(?:切换到 )?(?:default|默认配置)$/, exact: true })
    await defaultButton.click()
    await expect(personalButton).toHaveAttribute('aria-pressed', 'false', { timeout: 60_000 })
    input = await openSearch(page)
    await input.fill(PERSONAL_NEEDLE)
    await expect(page.getByText('未找到匹配结果。', { exact: true })).toBeVisible()
    await expect(resultRow(page, PERSONAL_SESSION)).toHaveCount(0)
    await input.fill(NEEDLE_A)
    await expect(resultRow(page, SESSION_A)).toContainText(TITLE_A)
    await page.screenshot({ path: testInfo.outputPath('default-profile-no-personal-leak.png') })
    expect(await collectErrorBanners(page)).toEqual([])

    // A full native restart, preserving only the isolated home and userData.
    rememberBackendPids(fixture)
    await fixture.app.close()
    const relaunched = await launchDesktop(isolatedEnv(fixture.sandbox))
    fixture.app = relaunched.app
    fixture.ownedProcesses.push(relaunched.app.process())
    fixture.page = relaunched.page
    page = fixture.page
    page.on('pageerror', error => pageErrors.push(error.message))
    await waitForAppReady(fixture, 120_000)
    input = await openSearch(page)
    await input.fill(NEEDLE_A)
    await expect(resultRow(page, SESSION_A).locator('[data-session-search-snippet]')).toContainText(NEEDLE_A)
    await resultRow(page, SESSION_A).getByRole('button').first().click()
    await expect(transcript(page)).toContainText(MESSAGE_A)
    await expect(transcript(page)).toContainText(FOLLOW_UP)
    await expect(transcript(page)).toContainText(REPLY)
    expect(JSON.stringify(await messagesApi(page, SESSION_A))).toContain(FOLLOW_UP)
    expect(pageErrors).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('continued-history-after-restart.png') })
    await testInfo.attach('isolated-candidate', {
      body: JSON.stringify({ appPath: await fixture.app.evaluate(({ app }) => app.getAppPath()), userData: fixture.sandbox.userDataDir, hermesHome: fixture.sandbox.hermesHome, pid: fixture.app.process().pid, sessions: [SESSION_A, SESSION_B] }),
      contentType: 'application/json'
    })
  })

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
  test('keeps the latest query, clears stale hits, shows empty results and preserves an unsent draft on cancel', async ({}, testInfo) => {
    fixture = await setup()
    const { page } = fixture
    const draft = '搜索取消后仍应完整保留的未发送中文草稿'
    await page.locator('[data-personal-product-nav]').getByRole('button', { name: '新建对话', exact: true }).click()
    const composer = page.locator('[contenteditable="true"]').filter({ visible: true }).last()
    await composer.click()
    await composer.pressSequentially(draft, { delay: 5 })
    await expect(composer).toHaveText(draft)
    const input = await openSearch(page)
    await input.fill(NEEDLE_A)
    await expect(resultRow(page, SESSION_A)).toBeVisible()
    await input.fill(NEEDLE_B)
    await input.fill(NEEDLE_A)
    await input.fill(NEEDLE_B)
    await expect(resultRow(page, SESSION_B).locator('[data-session-search-snippet]')).toContainText(NEEDLE_B)
    await expect(resultRow(page, SESSION_A)).toHaveCount(0)

    await input.fill('完全不存在的蓝鲸暗号')
    expect((await searchApi(page, '完全不存在的蓝鲸暗号')).results).toEqual([])
    await expect(page.getByText('未找到匹配结果。', { exact: true })).toBeVisible()
    await expect(page.locator('[data-session-search-snippet]')).toHaveCount(0)
    await input.fill(NEEDLE_A)
    await expect(resultRow(page, SESSION_A)).toBeVisible()
    await input.fill('')
    await expect(input).toHaveValue('')
    await expect(resultRow(page, 'acceptance-search-recent-104')).toBeVisible()
    await expect(page.locator('[data-session-search-snippet]')).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('cleared-search.png') })
    await page.keyboard.press('Escape')
    await expect(input).toBeHidden()
    await expect(composer).toBeVisible()
    await expect(composer).toHaveText(draft)
    expect((await searchApi(page, draft)).results).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('draft-preserved-after-search-cancel.png') })
  })

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
  test('opens the searched owner when two profiles reuse the same stored id and a pane is already open', async ({}, testInfo) => {
    fixture = await setup()
    const { page } = fixture
    expect((await searchApi(page, SHARED_DEFAULT_NEEDLE)).results.map(hit => hit.session_id)).toEqual([SHARED_SESSION])
    expect((await searchApi(page, SHARED_PERSONAL_NEEDLE, PERSONAL_PROFILE)).results.map(hit => hit.session_id)).toEqual([SHARED_SESSION])

    // Open default/S first, creating the old owner hint and retained pane.
    let input = await openSearch(page)
    await input.fill(SHARED_DEFAULT_NEEDLE)
    let row = resultRow(page, SHARED_SESSION)
    await expect(row).toContainText(SHARED_DEFAULT_TITLE)
    await expect(row.locator('[data-session-search-snippet]')).toContainText(SHARED_DEFAULT_NEEDLE)
    await row.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(SHARED_DEFAULT_BODY)
    await expect(transcript(page)).not.toContainText(SHARED_PERSONAL_BODY)
    await page.screenshot({ path: testInfo.outputPath('same-id-default-open-first.png') })

    const rail = page.locator('[data-slot="profile-rail"]')
    const personalButton = rail.getByRole('button', { name: PERSONAL_PROFILE, exact: true })
    await personalButton.click()
    await expect(personalButton).toHaveAttribute('aria-pressed', 'true', { timeout: 60_000 })
    input = await openSearch(page)
    await input.fill(SHARED_PERSONAL_NEEDLE)
    row = resultRow(page, SHARED_SESSION)
    await expect(row).toContainText(SHARED_PERSONAL_TITLE, { timeout: 60_000 })
    await expect(row.locator('[data-session-search-snippet]')).toContainText(SHARED_PERSONAL_NEEDLE)
    await row.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(SHARED_PERSONAL_BODY, { timeout: 60_000 })
    await expect(transcript(page)).not.toContainText(SHARED_DEFAULT_BODY)
    await page.screenshot({ path: testInfo.outputPath('same-id-personal-owner-opened.png') })

    await rail.getByRole('button', { name: /^(?:切换到 )?(?:default|默认配置)$/, exact: true }).click()
    await expect(personalButton).toHaveAttribute('aria-pressed', 'false', { timeout: 60_000 })
    input = await openSearch(page)
    await input.fill(SHARED_DEFAULT_NEEDLE)
    row = resultRow(page, SHARED_SESSION)
    await expect(row).toContainText(SHARED_DEFAULT_TITLE)
    await expect(row.locator('[data-session-search-snippet]')).toContainText(SHARED_DEFAULT_NEEDLE)
    await row.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(SHARED_DEFAULT_BODY)
    await expect(transcript(page)).not.toContainText(SHARED_PERSONAL_BODY)
    expect(await collectErrorBanners(page)).toEqual([])
    await page.screenshot({ path: testInfo.outputPath('same-id-default-owner-restored.png') })
    await testInfo.attach('same-id-owners', {
      body: JSON.stringify({ storedId: SHARED_SESSION, profiles: ['default', PERSONAL_PROFILE], titles: [SHARED_DEFAULT_TITLE, SHARED_PERSONAL_TITLE] }),
      contentType: 'application/json'
    })
  })

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
  test('legacy env remote keeps profile-only same-id ownership through opening and a real continued turn', async ({}, testInfo) => {
    fixture = await setupLegacyRemote()
    const { page } = fixture
    const initialConnection = await connectionMetadata(page)
    expect(initialConnection).toMatchObject({ origin: fixture.remoteOrigin, mode: 'remote', source: 'env', connectionId: null })
    expect(path.resolve(fixture.remoteHome!)).not.toBe(path.resolve(fixture.sandbox.hermesHome))

    let input = await openSearch(page)
    await input.fill(SHARED_DEFAULT_NEEDLE)
    let row = resultRow(page, SHARED_SESSION)
    await expect(row).toContainText(SHARED_DEFAULT_TITLE)
    await row.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(SHARED_DEFAULT_BODY)

    const rail = page.locator('[data-slot="profile-rail"]')
    const personalButton = rail.getByRole('button', { name: PERSONAL_PROFILE, exact: true })
    await personalButton.click()
    await expect(personalButton).toHaveAttribute('aria-pressed', 'true', { timeout: 60_000 })
    const personalConnection = await connectionMetadata(page, PERSONAL_PROFILE)
    expect(personalConnection).toMatchObject({ origin: fixture.remoteOrigin, mode: 'remote', source: 'env', connectionId: null })
    input = await openSearch(page)
    await input.fill(SHARED_PERSONAL_NEEDLE)
    row = resultRow(page, SHARED_SESSION)
    await expect(row).toContainText(SHARED_PERSONAL_TITLE, { timeout: 60_000 })
    await expect(row.locator('[data-session-search-snippet]')).toContainText(SHARED_PERSONAL_NEEDLE)
    await row.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(SHARED_PERSONAL_BODY, { timeout: 60_000 })
    await expect(transcript(page)).not.toContainText(SHARED_DEFAULT_BODY)

    const composer = page.locator('[contenteditable="true"]').filter({ visible: true }).last()
    await composer.click()
    await composer.pressSequentially(LEGACY_PERSONAL_FOLLOW_UP, { delay: 5 })
    await page.keyboard.press('Enter')
    await expect(transcript(page)).toContainText(REPLY, { timeout: 60_000 })
    await expect.poll(async () => JSON.stringify(await fixture!.remoteMessages!(SHARED_SESSION, PERSONAL_PROFILE)), { timeout: 30_000 }).toContain(LEGACY_PERSONAL_FOLLOW_UP)
    expect(JSON.stringify(await fixture.remoteMessages!(SHARED_SESSION, 'default'))).not.toContain(LEGACY_PERSONAL_FOLLOW_UP)
    expect(appHomeContainsText(fixture.sandbox, LEGACY_PERSONAL_FOLLOW_UP)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath('legacy-remote-personal-continued.png') })

    await rail.getByRole('button', { name: /^(?:切换到 )?(?:default|默认配置)$/, exact: true }).click()
    await expect(personalButton).toHaveAttribute('aria-pressed', 'false', { timeout: 60_000 })
    input = await openSearch(page)
    await input.fill(SHARED_DEFAULT_NEEDLE)
    row = resultRow(page, SHARED_SESSION)
    await expect(row).toContainText(SHARED_DEFAULT_TITLE)
    await row.getByRole('button').first().click()
    await expect(transcript(page)).toContainText(SHARED_DEFAULT_BODY)
    await expect(transcript(page)).not.toContainText(SHARED_PERSONAL_BODY)
    await expect(transcript(page)).not.toContainText(LEGACY_PERSONAL_FOLLOW_UP)
    expect(await collectErrorBanners(page)).toEqual([])
    fixture.remoteSockets = await page.evaluate(() =>
      (window as unknown as { __searchAcceptanceSockets: NonNullable<Fixture['remoteSockets']> }).__searchAcceptanceSockets
    )
    await page.screenshot({ path: testInfo.outputPath('legacy-remote-default-restored.png') })
    await testInfo.attach('legacy-remote-public-metadata', {
      body: JSON.stringify({ initialConnection, personalConnection, remoteHome: fixture.remoteHome, appHome: fixture.sandbox.hermesHome, httpStatus: fixture.remoteHttpStatus, servePid: fixture.ownedServeProcesses[0].pid, webSockets: fixture.remoteSockets }),
      contentType: 'application/json'
    })
    const remoteHost = new URL(fixture.remoteOrigin!).host
    expect(fixture.remoteSockets!.some(socket => new URL(socket.origin).host === remoteHost && socket.receivedFrames > 0)).toBe(true)
  })

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
  test('supplement: retries an injected API failure and rejects responses after switching or clearing', async ({}, testInfo) => {
    allowErrorBanners()
    testInfo.annotations.push({ type: 'fault-injection', description: 'Only this supplemental test changes native Node HTTP transport for the selected search query; all success responses and the persistence tests use the real local API.' })
    fixture = await setup()
    const { app, page } = fixture
    const input = await openSearch(page)

    try {
      await installSearchFault(app, NEEDLE_A, 'fail')
      await input.fill(NEEDLE_A)
      const error = page.getByRole('alert').filter({ hasText: '全文检索未完成' })
      await expect(error).toBeVisible()
      await page.screenshot({ path: testInfo.outputPath('injected-search-failure.png') })
      await releaseSearchFault(app)
      await page.getByRole('button', { name: '重试', exact: true }).click()
      await expect(resultRow(page, SESSION_A).locator('[data-session-search-snippet]')).toContainText(NEEDLE_A)
      await expect(error).toHaveCount(0)

      await input.fill('')
      await expect(resultRow(page, 'acceptance-search-recent-104')).toBeVisible()
      await installSearchFault(app, NEEDLE_A, 'hold')
      await input.fill(NEEDLE_A)
      await waitForHeldSearch(app)
      await input.fill(NEEDLE_B)
      await expect(resultRow(page, SESSION_B).locator('[data-session-search-snippet]')).toContainText(NEEDLE_B)
      await releaseSearchFault(app)
      // Give the released IPC response time to reach React after the new query.
      await page.waitForTimeout(300)
      await expect(resultRow(page, SESSION_A)).toHaveCount(0)
      await expect(resultRow(page, SESSION_B)).toBeVisible()

      await installSearchFault(app, NEEDLE_A, 'hold')
      await input.fill(NEEDLE_A)
      await waitForHeldSearch(app)
      await input.fill('')
      await releaseSearchFault(app)
      await page.waitForTimeout(300)
      await expect(input).toHaveValue('')
      await expect(page.locator('[data-session-search-snippet]')).toHaveCount(0)
      await expect(resultRow(page, 'acceptance-search-recent-104')).toBeVisible()
      await page.screenshot({ path: testInfo.outputPath('stale-search-response-cancelled.png') })
    } finally {
      await releaseSearchFault(app)
    }
  })
})
