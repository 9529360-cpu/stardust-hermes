/**
 * Native Electron acceptance for owner-qualified sessions and scheduled-jobs navigation.
 *
 * The local default and research profiles contain the same stored session id.
 * The newer research row is first in the unified sidebar list; selecting the
 * default row must still open that profile's transcript.
 */

import { spawnSync } from 'node:child_process'
import * as fs from 'node:fs'
import { createRequire } from 'node:module'
import * as path from 'node:path'

import { _electron } from '@playwright/test'

import type { DesktopMarketplaceSearchItem, DesktopMarketplaceThemeResult } from '@/global'

import {
  BATCH_CLARIFY_QUESTIONS,
  BATCH_CLARIFY_TRIGGER,
  MOCK_REPLY,
  startMockServer
} from '../../../tests-js/scripts/mock-server'

import { buildAppEnv, createSandbox, type MockBackendFixture, type Sandbox, waitForAppReady } from './fixtures'
import {
  allowErrorBanners,
  collectErrorBanners,
  type ElectronApplication,
  expect,
  installErrorBannerGuard,
  type Page,
  test
} from './test'

const DESKTOP_ROOT = path.resolve(import.meta.dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')

const TASK_ROOT = process.env.STARDUST_DESKTOP_E2E_TASK_ROOT ??
  'D:/Development/Temp/Stardust/task-center-owner-route-20261028'

const TASK_TEMP_ROOT = path.join(TASK_ROOT, 'temp')

const REPORT_ROOT =
  process.env.STARDUST_DESKTOP_E2E_REPORT_DIR ??
  'D:/Development/Reports/Stardust/task-center-owner-route-20261028/cwd-consistency-verification'

const OWNER_SESSION_ID = 'e2e-shared-session-owner'
const PANE_STATES_KEY = 'hermes.desktop.paneStates.v1'
const PROFILE_TWIN_MARKER = 'RESEARCH_PROFILE_TRANSCRIPT_DECOY'
const OWNER_MARKER = 'DEFAULT_PROFILE_TRANSCRIPT_TARGET'
const CWD_BUSY_PROMPT = 'E2E_CWD_BUSY_HOLD'
const CWD_BUSY_REPLY = 'The held turn is released.'

const MARKETPLACE_SEARCH_FIXTURE: DesktopMarketplaceSearchItem = {
  extensionId: 'stardust.fixture-dusk',
  displayName: 'Stardust Dusk Fixture',
  publisher: 'Stardust Fixtures',
  description: 'Local theme fixture for the import flow.',
  installs: 1234
}

const MARKETPLACE_THEME_FIXTURE: DesktopMarketplaceThemeResult = {
  extensionId: MARKETPLACE_SEARCH_FIXTURE.extensionId,
  displayName: MARKETPLACE_SEARCH_FIXTURE.displayName,
  themes: [
    {
      label: 'Dusk Light',
      uiTheme: 'vs',
      contents: JSON.stringify({
        name: 'Dusk Light',
        type: 'light',
        colors: {
          'editor.background': '#f9fafb',
          'editor.foreground': '#20242b',
          'sideBar.background': '#f1f3f6',
          'button.background': '#174ea6'
        }
      })
    },
    {
      label: 'Dusk Dark',
      uiTheme: 'vs-dark',
      contents: JSON.stringify({
        name: 'Dusk Dark',
        type: 'dark',
        colors: {
          'editor.background': '#171923',
          'editor.foreground': '#e4e6ef',
          'sideBar.background': '#1d202a',
          'button.background': '#9bbcff'
        }
      })
    }
  ]
}

const ISOLATED_APP_ROOT = path.join(TASK_ROOT, 'project-cwd-electron')
const ISOLATED_DIST_DIR = path.join(ISOLATED_APP_ROOT, 'dist')
const BASE_ELECTRON_RUNTIME = 'D:/Development/Temp/Stardust/stream-stall-diagnosis/electron-dist'
const BACKEND_VENV = path.resolve(REPO_ROOT, '../../../.venv')
const BACKEND_PYTHON = path.join(BACKEND_VENV, 'Scripts/python.exe')
const BACKEND_SITE_PACKAGES = path.join(BACKEND_VENV, 'Lib/site-packages')
const DIST_RUNTIME_GUARD = path.join(ISOLATED_DIST_DIR, '.runtime-dir')
const APP_ROOT_MARKER = path.join(ISOLATED_APP_ROOT, '.app-root')
const APP_ROOT_MARKER_CONTENTS = 'Electron app root owned by task-center-owner-route-20261028\n'
const DIST_RUNTIME_GUARD_CONTENTS = 'dist outputs stay in this task directory\n'

const sharedNodeModules = path.resolve(
  path.dirname(createRequire(path.join(DESKTOP_ROOT, 'package.json')).resolve('node-pty')),
  '..',
  '..'
)

function prepareIsolatedAppRoot(): void {
  fs.mkdirSync(ISOLATED_DIST_DIR, { recursive: true })

  const existingAppRootMarker = fs.lstatSync(APP_ROOT_MARKER, { throwIfNoEntry: false })

  if (existingAppRootMarker?.isSymbolicLink() || (existingAppRootMarker && !existingAppRootMarker.isFile())) {
    throw new Error(`Refusing to use unexpected app root marker at ${APP_ROOT_MARKER}`)
  }

  if (existingAppRootMarker && fs.readFileSync(APP_ROOT_MARKER, 'utf8') !== APP_ROOT_MARKER_CONTENTS) {
    throw new Error(`Refusing to use unexpected app root marker at ${APP_ROOT_MARKER}`)
  }

  if (!existingAppRootMarker) {
    fs.writeFileSync(APP_ROOT_MARKER, APP_ROOT_MARKER_CONTENTS, { flag: 'wx' })
  }

  if (fs.existsSync(DIST_RUNTIME_GUARD)) {
    if (fs.readFileSync(DIST_RUNTIME_GUARD, 'utf8') !== DIST_RUNTIME_GUARD_CONTENTS) {
      throw new Error(`Refusing to use unexpected dist guard at ${DIST_RUNTIME_GUARD}`)
    }
  } else {
    fs.writeFileSync(DIST_RUNTIME_GUARD, DIST_RUNTIME_GUARD_CONTENTS, { flag: 'wx' })
  }

  const appModules = path.join(ISOLATED_APP_ROOT, 'node_modules')
  const appModulesStat = fs.lstatSync(appModules, { throwIfNoEntry: false })

  if (appModulesStat?.isSymbolicLink()) {
    const rawTarget = fs.readlinkSync(appModules)
    const target = fs.realpathSync(appModules)

    if (target !== fs.realpathSync(sharedNodeModules)) {
      throw new Error(`Refusing to use unexpected app dependency link at ${appModules}`)
    }
  } else if (appModulesStat) {
    throw new Error(`Refusing to replace unexpected app dependencies at ${appModules}`)
  } else {
    fs.symlinkSync(sharedNodeModules, appModules, 'junction')
  }

  fs.copyFileSync(path.join(DESKTOP_ROOT, 'package.json'), path.join(ISOLATED_APP_ROOT, 'package.json'))
}

function seedTaskOwnerSession(
  hermesHome: string,
  profile: 'default' | 'research',
  marker: string,
  cwd: string,
  timestampOffset = 0
): void {
  const python = process.env.STARDUST_DESKTOP_PYTHON

  if (!python) {
    throw new Error('STARDUST_DESKTOP_PYTHON must name the repository backend interpreter')
  }

  const result = spawnSync(
    python,
    [
      '-B',
      '-c',
      `
import pathlib, sys, time
from hermes_state import SessionDB
root = pathlib.Path(sys.argv[1])
profile = sys.argv[2]
marker = sys.argv[3]
cwd = sys.argv[4]
timestamp = time.time() + float(sys.argv[5])
home = root if profile == 'default' else root / 'profiles' / profile
home.mkdir(parents=True, exist_ok=True)
db = SessionDB(home / 'state.db')
try:
    db.create_session('${OWNER_SESSION_ID}', source='desktop', model='mock-model', cwd=cwd)
    db.set_session_title('${OWNER_SESSION_ID}', marker)
    db.append_message('${OWNER_SESSION_ID}', role='user', content=marker, timestamp=timestamp)
finally:
    db.close()
`,
      hermesHome,
      profile,
      marker,
      cwd,
      String(timestampOffset)
    ],
    {
      cwd: REPO_ROOT,
      encoding: 'utf8',
      env: {
        ...process.env,
        HERMES_HOME: hermesHome,
        PYTHONPATH: `${REPO_ROOT}${path.delimiter}${BACKEND_SITE_PACKAGES}`,
        PYTHONDONTWRITEBYTECODE: '1',
        STARDUST_HOME: hermesHome,
        VIRTUAL_ENV: BACKEND_VENV
      },
      timeout: 30_000,
      windowsHide: true
    }
  )

  if (result.error || result.status !== 0) {
    throw new Error(`Could not seed isolated profile-owned session data: ${result.error?.message ?? result.stderr}`)
  }
}

function normalizedHostPath(value: string): string {
  return value
    .replace(/\\/g, '/')
    .replace(/^\/([a-z])\//i, (_, drive: string) => `${drive.toUpperCase()}:/`)
    .toLowerCase()
}

function seedProfiles(home: string, profiles: string[]): void {
  for (const profile of profiles) {
    const directory = path.join(home, 'profiles', profile)
    fs.mkdirSync(directory, { recursive: true })
    fs.writeFileSync(path.join(directory, 'config.yaml'), '', 'utf8')
  }
}

async function capture(page: Page, name: string): Promise<void> {
  fs.mkdirSync(REPORT_ROOT, { recursive: true })
  await page.screenshot({ path: path.join(REPORT_ROOT, `${name}.png`) })
}

test.describe('native desktop acceptance — profile-qualified session routing', () => {
  test.describe.configure({ mode: 'serial' })
  let mock: Awaited<ReturnType<typeof startMockServer>>
  let sandbox: Sandbox
  let app: ElectronApplication
  let page: Page

  test.beforeAll(async () => {
    test.setTimeout(240_000)
    fs.mkdirSync(TASK_ROOT, { recursive: true })
    fs.mkdirSync(TASK_ROOT, { recursive: true })
    fs.mkdirSync(TASK_TEMP_ROOT, { recursive: true })
    fs.mkdirSync(REPORT_ROOT, { recursive: true })
    fs.mkdirSync(ISOLATED_APP_ROOT, { recursive: true })
    fs.mkdirSync(ISOLATED_DIST_DIR, { recursive: true })
    prepareIsolatedAppRoot()
    const runnerTempRoot = path.join(TASK_TEMP_ROOT, 'runner-temp')
    fs.mkdirSync(runnerTempRoot, { recursive: true })
    process.env.STARDUST_DESKTOP_USER_DATA_DIR = runnerTempRoot
    process.env.TEMP = runnerTempRoot
    process.env.TMP = runnerTempRoot
    process.env.TMPDIR = runnerTempRoot
    process.env.STARDUST_DESKTOP_E2E_TEMP_ROOT = runnerTempRoot
    const mockTempRoot = path.join(runnerTempRoot, 'mock-server')
    fs.mkdirSync(mockTempRoot, { recursive: true })
    mock = await startMockServer({
      holdFirstStreamForPrompt: CWD_BUSY_PROMPT,
      replyForPrompt: prompt => (prompt.includes(CWD_BUSY_PROMPT) ? CWD_BUSY_REPLY : MOCK_REPLY)
    })
    sandbox = createSandbox('task-center-owner-routing')
    const tempRoot = path.join(sandbox.root, 'temp')
    const projectA = path.join(sandbox.root, 'project-A')
    const projectB = path.join(sandbox.root, 'project-B')
    fs.mkdirSync(tempRoot, { recursive: true })
    fs.mkdirSync(projectA, { recursive: true })
    fs.mkdirSync(projectB, { recursive: true })
    process.env.STARDUST_DESKTOP_PYTHON = BACKEND_PYTHON
    process.env.STARDUST_DESKTOP_HERMES_ROOT = REPO_ROOT
    process.env.STARDUST_DESKTOP_DIST_DIR = ISOLATED_DIST_DIR
    process.env.STARDUST_DESKTOP_WEB_DIST = ISOLATED_DIST_DIR
    process.env.STARDUST_DESKTOP_USER_DATA_DIR = sandbox.userDataDir
    process.env.STARDUST_GUEST_ONBOARDING = '0'
    process.env.STARDUST_SKIP_INTRO = '1'
    process.env.STARDUST_DESKTOP_IGNORE_EXISTING = '1'
    process.env.STARDUST_DESKTOP_APP_NAME = 'TaskCenterOwnerRoutingE2E'
    process.env.STARDUST_DESKTOP_PROFILE = 'default'

    const providerConfig = (language: string, cwd: string) =>
      `model:\n  default: mock-model\n  provider: openai-api\n  base_url: ${mock.url}/v1\n  api_mode: chat_completions\nproviders:\n  openai-api:\n    models:\n      mock-model: {}\nauxiliary:\n  title_generation:\n    enabled: false\nterminal:\n  cwd: ${JSON.stringify(cwd)}\ndisplay:\n  language: ${language}\n`

    fs.writeFileSync(path.join(sandbox.hermesHome, 'config.yaml'), providerConfig('zh', projectA), 'utf8')
    fs.writeFileSync(path.join(sandbox.hermesHome, '.env'), 'OPENAI_API_KEY=e2e-mock-key\n', 'utf8')
    seedProfiles(sandbox.hermesHome, ['research'])
    fs.writeFileSync(
      path.join(sandbox.hermesHome, 'profiles', 'research', 'profile.yaml'),
      'display_name: 研究伙伴\ndescription: 协助梳理资料、比较证据并总结发现。\n',
      'utf8'
    )
    fs.writeFileSync(
      path.join(sandbox.hermesHome, 'profiles', 'research', 'config.yaml'),
      providerConfig('zh', projectA),
      'utf8'
    )
    fs.writeFileSync(
      path.join(sandbox.hermesHome, 'profiles', 'research', '.env'),
      'OPENAI_API_KEY=e2e-mock-key\n',
      'utf8'
    )
    seedTaskOwnerSession(sandbox.hermesHome, 'default', OWNER_MARKER, projectA)
    seedTaskOwnerSession(sandbox.hermesHome, 'research', PROFILE_TWIN_MARKER, projectB, 30)

    const projectsDatabase = spawnSync(
      BACKEND_PYTHON,
      [
        '-B',
        '-c',
        `import os, sys\nfrom hermes_cli import projects_db\nwith projects_db.connect_closing() as db:\n    projects_db.create_project(db, name='Project A', folders=[sys.argv[1]], primary_path=sys.argv[1])\n    projects_db.create_project(db, name='Project B', folders=[sys.argv[2]], primary_path=sys.argv[2])`,
        projectA,
        projectB
      ],
      {
        cwd: REPO_ROOT,
        encoding: 'utf8',
        env: {
          ...process.env,
          HERMES_HOME: sandbox.hermesHome,
          PYTHONPATH: `${REPO_ROOT}${path.delimiter}${BACKEND_SITE_PACKAGES}`,
          PYTHONDONTWRITEBYTECODE: '1',
          STARDUST_HOME: sandbox.hermesHome,
          VIRTUAL_ENV: BACKEND_VENV
        },
        timeout: 30_000,
        windowsHide: true
      }
    )

    if (projectsDatabase.error || projectsDatabase.status !== 0) {
      throw new Error(`Could not seed isolated projects: ${projectsDatabase.error?.message ?? projectsDatabase.stderr}`)
    }

    fs.writeFileSync(
      path.join(sandbox.userDataDir, 'active-profile.json'),
      JSON.stringify({ profile: 'default' }),
      'utf8'
    )
    const candidatePackage = path.join(ISOLATED_DIST_DIR, 'electron-candidate-package.json')

    if (!fs.existsSync(candidatePackage)) {
      fs.copyFileSync(path.join(DESKTOP_ROOT, 'package.json'), candidatePackage)
    }

    for (const resource of ['assets', 'public']) {
      const link = path.join(ISOLATED_APP_ROOT, resource)
      const stat = fs.lstatSync(link, { throwIfNoEntry: false })
      const expectedTarget = path.join(DESKTOP_ROOT, resource)

      if (stat?.isSymbolicLink()) {
        if (path.resolve(fs.readlinkSync(link)) !== expectedTarget) {
          throw new Error(`Refusing to use unexpected resource link at ${link}`)
        }
      } else if (stat) {
        throw new Error(`Refusing to replace unexpected app resource at ${link}`)
      } else {
        fs.symlinkSync(expectedTarget, link, 'junction')
      }
    }

    const env = buildAppEnv(sandbox, {
      STARDUST_DESKTOP_USER_DATA_DIR: sandbox.userDataDir,
      ELECTRON_OVERRIDE_DIST_PATH: process.env.ELECTRON_OVERRIDE_DIST_PATH || BASE_ELECTRON_RUNTIME,
      NODE_PATH: [path.join(ISOLATED_APP_ROOT, 'node_modules'), path.resolve(REPO_ROOT, 'node_modules')].join(
        path.delimiter
      ),
      STARDUST_GUEST_ONBOARDING: '0',
      STARDUST_SKIP_INTRO: '1',
      STARDUST_DESKTOP_DIST_DIR: ISOLATED_DIST_DIR,
      STARDUST_DESKTOP_E2E_TEMP_ROOT: tempRoot,
      STARDUST_DESKTOP_HERMES_ROOT: REPO_ROOT,
      STARDUST_DESKTOP_PYTHON: BACKEND_PYTHON,
      STARDUST_DESKTOP_WEB_DIST: ISOLATED_DIST_DIR,
      STARDUST_HOME: sandbox.hermesHome,
      TEMP: tempRoot,
      TMP: tempRoot,
      TMPDIR: tempRoot
    })

    const electronLog = fs.createWriteStream(path.join(REPORT_ROOT, 'electron.log'), { flags: 'a' })
    app = await _electron.launch({
      executablePath: path.join(BASE_ELECTRON_RUNTIME, 'electron.exe'),
      args: [ISOLATED_APP_ROOT, '--disable-gpu', '--no-sandbox'],
      cwd: ISOLATED_APP_ROOT,
      env: {
        ...env,
        NODE_PATH: [path.join(ISOLATED_APP_ROOT, 'node_modules'), ...env.NODE_PATH.split(path.delimiter)].join(
          path.delimiter
        )
      }
    })
    app.process().stdout?.pipe(electronLog, { end: false })
    app.process().stderr?.pipe(electronLog, { end: false })
    page = await app.firstWindow({ timeout: 15_000 })
    electronLog.end()
    installErrorBannerGuard(page)
    page.on('console', message =>
      fs.appendFileSync(path.join(REPORT_ROOT, 'renderer.log'), `${message.type()}: ${message.text()}\n`)
    )
    await waitForAppReady({ app, page } as MockBackendFixture, 60_000).catch(async error => {
      const diagnostic = await page
        .evaluate(async () => {
          const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

          const api = async (path: string, profile?: string) =>
            desktop.api({ path, profile }).catch((value: unknown) => ({ error: String(value) }))

          const request = async (profile: string | null, method: string, params: Record<string, unknown> = {}) => {
            try {
              const connection = await desktop.getConnection(profile)
              const wsResult = await desktop.getGatewayWsUrl(profile)
              const wsUrl = typeof wsResult === 'string' ? wsResult : wsResult.ok ? wsResult.wsUrl : null

              if (!wsUrl) {
                throw new Error(typeof wsResult === 'string' ? 'Missing gateway WebSocket URL' : wsResult.error)
              }

              const socket = new WebSocket(wsUrl)
              await new Promise<void>((resolve, reject) => {
                socket.addEventListener('open', () => resolve(), { once: true })
                socket.addEventListener('error', () => reject(new Error('Gateway websocket failed')), { once: true })
              })

              const result = await new Promise<unknown>((resolve, reject) => {
                const id = `task-owner-diagnostic-${method}-${profile ?? 'default'}`
                const timer = window.setTimeout(() => reject(new Error(`${method} diagnostic timed out`)), 15_000)
                socket.addEventListener('message', event => {
                  try {
                    const frame = JSON.parse(String(event.data)) as Record<string, unknown>

                    if (frame.id !== id) {
                      return
                    }

                    window.clearTimeout(timer)

                    if (frame.error) {
                      reject(new Error(JSON.stringify(frame.error)))
                    } else {
                      resolve(frame.result)
                    }

                    socket.close()
                  } catch (error) {
                    window.clearTimeout(timer)
                    reject(error)
                    socket.close()
                  }
                })
                socket.send(JSON.stringify({ id, jsonrpc: '2.0', method, params }))
              })

              return { profile, baseUrl: connection.baseUrl, result }
            } catch (value) {
              return { profile, error: String(value) }
            }
          }

          const [defaultConfig, researchConfig, defaultStatus, researchStatus, defaultRuntime, researchRuntime] =
            await Promise.all([
              api('/api/config', 'default'),
              api('/api/config', 'research'),
              request(null, 'setup.status'),
              request('research', 'setup.status', { profile: 'research' }),
              request(null, 'setup.runtime_check'),
              request('research', 'setup.runtime_check', { profile: 'research' })
            ])

          return {
            href: location.href,
            body: document.body.innerText,
            defaultConfig,
            researchConfig,
            defaultStatus,
            researchStatus,
            defaultRuntime,
            researchRuntime
          }
        })
        .catch((value: unknown) => ({ error: String(value) }))

      fs.writeFileSync(
        path.join(REPORT_ROOT, 'boot-diagnostics.json'),
        JSON.stringify(
          {
            error: String(error),
            diagnostic,
            localConfig: fs.readFileSync(path.join(sandbox.hermesHome, 'config.yaml'), 'utf8'),
            researchConfig: fs.readFileSync(path.join(sandbox.hermesHome, 'profiles/research/config.yaml'), 'utf8'),
            electronEnv: {
              cwd: app.process().spawnargs,
              STARDUST_HOME: env.STARDUST_HOME,
              STARDUST_DESKTOP_HERMES_ROOT: env.STARDUST_DESKTOP_HERMES_ROOT,
              STARDUST_DESKTOP_PYTHON: env.STARDUST_DESKTOP_PYTHON,
              NODE_OPTIONS: env.NODE_OPTIONS,
              PYTHONPATH: env.PYTHONPATH,
              VIRTUAL_ENV: env.VIRTUAL_ENV,
              TEMP: env.TEMP,
              TMP: env.TMP
            },
            ownership: fs.existsSync(path.join(sandbox.userDataDir, 'backend-ownership.json'))
              ? fs.readFileSync(path.join(sandbox.userDataDir, 'backend-ownership.json'), 'utf8')
              : null
          },
          null,
          2
        )
      )
      throw error
    })
    await capture(page, '00-ready-chat-home')
    await expect(page.locator('[data-statusbar-item="gateway-health"]')).toContainText('就绪', {
      timeout: 120_000
    })
    fs.writeFileSync(
      path.join(REPORT_ROOT, 'runtime-isolation.json'),
      JSON.stringify(
        {
          appRoot: ISOLATED_APP_ROOT,
          isolatedDistDir: ISOLATED_DIST_DIR,
          backendRoot: REPO_ROOT,
          candidateHead: spawnSync('git', ['rev-parse', 'HEAD'], { cwd: REPO_ROOT, encoding: 'utf8' }).stdout.trim(),
          electronRuntime: BASE_ELECTRON_RUNTIME,
          hermesHome: sandbox.hermesHome,
          tempRoot: sandbox.root,
          userData: sandbox.userDataDir
        },
        null,
        2
      )
    )
  })

  test.afterAll(async () => {
    await app?.close().catch(() => undefined)
    await mock?.close().catch(() => undefined)
    sandbox?.cleanup()
  })

  test('navigates chat, scheduled jobs, appearance, Tools, Messaging, and Connectors', async () => {
    test.setTimeout(180_000)

    const nativeProjectCwdCalls: Array<{ cwd: string; sessionId: string }> = []
    const nativeSessionRpcFrames: Array<Record<string, unknown>> = []
    const rpcMethodBySocketAndId = new Map<string, string>()
    const sidebarRows = page.locator('[data-personal-sidebar-shell] [data-hermes-context-menu-trigger]')
    const rowForMarker = (marker: string) => sidebarRows.filter({ hasText: marker })

    const ownerRow = rowForMarker(OWNER_MARKER)
    const researchRow = rowForMarker(PROFILE_TWIN_MARKER)

    const rowIsSelected = (row: ReturnType<typeof page.locator>) =>
      row.evaluateAll(elements => elements.some(element => element.classList.contains('bg-(--ui-row-active-background)')))

    const readCwdObservation = async () =>
      page.evaluate(async sessionId => {
        const root = window as typeof window & {
          __HERMES_SESSION_TILES__?: { sessions?: () => unknown; states?: () => unknown; tiles?: () => unknown }
        }
        const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

        return {
          currentCwd: await desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' }).then(
            (detail: { cwd?: string }) => detail.cwd,
            (error: unknown) => ({ error: String(error) })
          ),
          selectedStoredSessionId: document.querySelector('[data-chat-surface][data-session-anchor="workspace"]')
            ?.getAttribute('data-session-id') ?? null,
          sessions: root.__HERMES_SESSION_TILES__?.sessions?.() ?? null,
          states: root.__HERMES_SESSION_TILES__?.states?.() ?? null,
          tiles: root.__HERMES_SESSION_TILES__?.tiles?.() ?? null
        }
      }, OWNER_SESSION_ID)

    const writeCwdDiagnostics = async (stage: string) => {
      const observation = await readCwdObservation()
      const snapshot = { observation, gatewayFrameCalls: nativeProjectCwdCalls }

      fs.writeFileSync(path.join(REPORT_ROOT, `cwd-${stage}.json`), JSON.stringify(snapshot, null, 2))

      return snapshot
    }

    const showAllProfiles = page.locator('[data-slot="profile-rail"] button[aria-label="显示全部配置档案"]')
    const switchToDefault = page.locator('[data-slot="profile-rail"] button[aria-label="切换到 default"]')

    const restoreAllProfiles = async (row: ReturnType<typeof page.locator>) => {
      if (await row.isVisible().catch(() => false)) {
        return
      }

      if (await switchToDefault.isVisible().catch(() => false)) {
        await switchToDefault.click()
        await expect(showAllProfiles).toBeVisible()
      }

      await showAllProfiles.click()
      await expect(row).toBeVisible()
    }

    const nav = page.locator('[data-personal-product-nav]')
    const scheduledJobs = page.locator('[data-cron-page]')
    await expect(nav.getByRole('button', { name: '新建对话', exact: true })).toBeVisible()
    await capture(page, '01-chat-home')

    const sidebarSnapshot = await page.evaluate(async () => {
      const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

      const response = await desktop.api({
        path: '/api/profiles/sessions/sidebar?recents_profile=all&recents_limit=100&cron_limit=50&messaging_limit=100',
        profile: 'default'
      })

      return (response.recents?.sessions ?? []).map((session: { id: string; profile?: string; title?: string }) => ({
        id: session.id,
        profile: session.profile,
        title: session.title
      }))
    })

    fs.writeFileSync(path.join(REPORT_ROOT, 'sidebar-api-snapshot.json'), JSON.stringify(sidebarSnapshot, null, 2))
    expect(sidebarSnapshot).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: OWNER_SESSION_ID, profile: 'default', title: OWNER_MARKER }),
        expect.objectContaining({ id: OWNER_SESSION_ID, profile: 'research', title: PROFILE_TWIN_MARKER })
      ])
    )

    if (await switchToDefault.isVisible()) {
      await switchToDefault.click()
      await expect(page.getByRole('button', { name: '显示全部配置档案', exact: true })).toBeVisible()
    }

    const allProfiles = page.getByRole('button', { name: '显示全部配置档案', exact: true })
    await expect(allProfiles).toBeVisible()

    const apiCapture = await app.evaluate(({ ipcMain }) => {
      type ApiRequest = { method?: string; path?: string; profile?: string }
      type ApiHandler = (event: unknown, request: ApiRequest) => Promise<unknown>
      const handlers = (ipcMain as unknown as { _invokeHandlers?: Map<string, ApiHandler> })._invokeHandlers
      const original = handlers?.get('stardust:api')

      const calls: Array<{
        profile?: string
        requestPath?: string
        sessions: Array<{ id: string; profile?: string; title?: string }>
      }> = []

      const requestScopes: Array<{ method?: string; path?: string; profile?: string }> = []

      if (!handlers || !original) {
        return { available: false, handlers: handlers ? [...handlers.keys()] : [] }
      }

      handlers.set('stardust:api', async (event, request) => {
        requestScopes.push({ method: request.method, path: request.path, profile: request.profile })

        const response = (await original(event, request)) as {
          recents?: { sessions?: Array<{ id: string; profile?: string; title?: string }> }
        }

        if (request.path?.includes('/api/profiles/sessions/sidebar')) {
          calls.push({
            profile: request.profile,
            requestPath: request.path,
            sessions: (response.recents?.sessions ?? []).map(({ id, profile, title }) => ({ id, profile, title }))
          })
        }

        return response
      })
      ;(
        globalThis as typeof globalThis & {
          __taskOwnerApiCalls?: typeof calls
          __taskOwnerRequestScopes?: typeof requestScopes
        }
      ).__taskOwnerApiCalls = calls
      ;(
        globalThis as typeof globalThis & { __taskOwnerRequestScopes?: typeof requestScopes }
      ).__taskOwnerRequestScopes = requestScopes

      return { available: true, handlers: [...handlers.keys()].filter(key => key === 'stardust:api') }
    })

    const profileTwin = page.getByText(PROFILE_TWIN_MARKER, { exact: true })
    const ownerSession = page.getByText(OWNER_MARKER, { exact: true })

    if (process.env.STARDUST_DESKTOP_E2E_PROJECT_CWD_ONLY !== '1') {
      await allProfiles.click()
      await expect(profileTwin).toBeVisible({ timeout: 30_000 })
      await page.waitForTimeout(750)
    }

    const mainApiCapture = await app.evaluate(() => {
      const root = globalThis as typeof globalThis & {
        __taskOwnerApiCaptureError?: string
        __taskOwnerApiCalls?: unknown[]
      }

      return { calls: root.__taskOwnerApiCalls ?? [], error: root.__taskOwnerApiCaptureError ?? null }
    })

    const profileDebug = await page.evaluate(captured => {
      const allRows = [...document.querySelectorAll('button')]
        .map(button => button.getAttribute('aria-label') || button.textContent?.trim() || '')
        .filter(label => label.includes('PROFILE_TRANSCRIPT'))

      const bridgeCalls = (
        window as typeof window & {
          stardustDesktop?: { api?: (request: { path?: string; profile?: string }) => Promise<unknown> }
        }
      ).stardustDesktop

      const apiCapture = captured.available
        ? {
            ...captured,
            calls: (globalThis as typeof globalThis & { __taskOwnerApiCalls?: unknown[] }).__taskOwnerApiCalls
          }
        : captured

      return {
        allRows,
        apiCapture: { ...apiCapture, main: captured },
        bridgeApiType: typeof bridgeCalls?.api,
        profileFilter: localStorage.getItem('hermes.desktop.sidebarProfileFilter'),
        showAllProfiles: localStorage.getItem('hermes.desktop.showAllProfiles'),
        pressedProfileButtons: [
          ...document.querySelectorAll('[data-slot="profile-rail"] button[aria-pressed="true"]')
        ].map(button => button.getAttribute('aria-label'))
      }
    }, apiCapture)

    fs.writeFileSync(
      path.join(REPORT_ROOT, 'profile-scope-debug.json'),
      JSON.stringify({ ...profileDebug, mainApiCapture }, null, 2)
    )
    await expect(ownerSession).toBeVisible({ timeout: 30_000 })

    if (process.env.STARDUST_DESKTOP_E2E_PROJECT_CWD_ONLY !== '1') {
      const researchFirst = await profileTwin.evaluate((element, ownerText) => {
        const owner = [...document.querySelectorAll('*')].find(node => node.textContent?.trim() === ownerText)

        return Boolean(owner && element.compareDocumentPosition(owner) & Node.DOCUMENT_POSITION_FOLLOWING)
      }, OWNER_MARKER)

      expect(researchFirst).toBe(true)
    }

    const ownerSelectionRpcFrames: Array<Record<string, unknown>> = []
    page.on('websocket', socket => {
      const socketProfile = new URL(socket.url()).searchParams.get('profile') ?? 'default'
      const methodsById = new Map<string, string>()

      socket.on('framesent', frame => {
        try {
          const batch = JSON.parse(String(frame.payload)) as unknown
          for (const request of Array.isArray(batch) ? batch : [batch]) {
            if (!request || typeof request !== 'object' || !('method' in request) || !('id' in request)) continue
            const method = String(request.method)
            const id = String(request.id)
            methodsById.set(id, method)
            if (['session.resume', 'session.activate'].includes(method)) {
              ownerSelectionRpcFrames.push({
                direction: 'send',
                method,
                params: 'params' in request ? request.params : null,
                socketProfile
              })
            }
          }
        } catch {
          // Ignore non-JSON and unrelated gateway frames.
        }
      })

      socket.on('framereceived', frame => {
        try {
          const batch = JSON.parse(String(frame.payload)) as unknown
          for (const response of Array.isArray(batch) ? batch : [batch]) {
            if (!response || typeof response !== 'object' || !('id' in response)) continue
            const id = String(response.id)
            const method = methodsById.get(id)
            if (!method || !['session.resume', 'session.activate'].includes(method)) continue
            const result = 'result' in response ? response.result as Record<string, unknown> | undefined : undefined
            ownerSelectionRpcFrames.push({
              direction: 'receive',
              method,
              result: result ? { sessionId: result.session_id, storedId: result.session_key, cwd: (result.info as Record<string, unknown> | undefined)?.cwd } : null,
              socketProfile
            })
          }
        } catch {
          // Ignore non-JSON and unrelated gateway frames.
        }
      })
    })

    await ownerSession.click()

    const transcript = page
      .locator('[data-chat-surface]')
      .filter({ visible: true })
      .last()
      .locator('[data-slot="aui_thread-viewport"]')

    await expect(transcript.getByText(OWNER_MARKER, { exact: true })).toBeVisible({ timeout: 45_000 })
    await expect(transcript.getByText(PROFILE_TWIN_MARKER, { exact: true })).toHaveCount(0)
    fs.writeFileSync(
      path.join(REPORT_ROOT, 'owner-selection-rpc-frames.json'),
      JSON.stringify(ownerSelectionRpcFrames, null, 2)
    )
    await expect.poll(() => rowIsSelected(ownerRow)).toBe(true)
    await expect.poll(() => rowIsSelected(researchRow)).toBe(false)

    if (process.env.STARDUST_DESKTOP_E2E_PROJECT_CWD_ONLY !== '1') {
      await restoreAllProfiles(researchRow)
      await researchRow.click()
      await expect(transcript.getByText(PROFILE_TWIN_MARKER, { exact: true })).toBeVisible({ timeout: 45_000 })
      await expect(transcript.getByText(OWNER_MARKER, { exact: true })).toHaveCount(0)
      await expect.poll(() => rowIsSelected(researchRow)).toBe(true)
      await expect.poll(() => rowIsSelected(ownerRow)).toBe(false)

      await restoreAllProfiles(ownerRow)
      await ownerRow.click()
      await expect(transcript.getByText(OWNER_MARKER, { exact: true })).toBeVisible({ timeout: 45_000 })
      await expect(transcript.getByText(PROFILE_TWIN_MARKER, { exact: true })).toHaveCount(0)
      await expect.poll(() => rowIsSelected(ownerRow)).toBe(true)
      await expect.poll(() => rowIsSelected(researchRow)).toBe(false)
    }

    await capture(page, '02-default-profile-transcript')

    if (process.env.STARDUST_DESKTOP_E2E_PROJECT_CWD_ONLY !== '1' && process.env.STARDUST_DESKTOP_E2E_OWNER_SELECTION_ONLY === '1') {
      fs.writeFileSync(
        path.join(REPORT_ROOT, 'owner-selection-native-check.json'),
        JSON.stringify(
          {
            candidates: [
              { row: OWNER_MARKER, selected: true, transcript: OWNER_MARKER },
              { row: PROFILE_TWIN_MARKER, selected: false, transcript: OWNER_MARKER },
              { row: PROFILE_TWIN_MARKER, selected: true, transcript: PROFILE_TWIN_MARKER },
              { row: OWNER_MARKER, selected: false, transcript: PROFILE_TWIN_MARKER },
              { row: OWNER_MARKER, selected: true, transcript: OWNER_MARKER },
              { row: PROFILE_TWIN_MARKER, selected: false, transcript: OWNER_MARKER }
            ],
            result: 'passed'
          },
          null,
          2
        )
      )

      return
    }

    const scheduledJobsNav = nav.getByRole('button', { name: '定时任务', exact: true })
    await scheduledJobsNav.click()
    await expect(scheduledJobsNav).toHaveAttribute('aria-current', 'page')
    await expect(scheduledJobs).toBeVisible({ timeout: 30_000 })
    await expect(scheduledJobs.getByRole('heading', { name: '定时任务', exact: true })).toBeVisible()
    const createJob = scheduledJobs.getByRole('button', { name: '新建定时任务', exact: true })
    await expect(createJob).toBeVisible({ timeout: 30_000 })
    await createJob.click()
    const createDialog = page.getByRole('dialog')
    await expect(createDialog.getByRole('heading', { name: '新建定时任务' })).toBeVisible()
    await expect(createDialog.getByLabel('提示词')).toBeVisible()
    await createDialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(createDialog).toHaveCount(0)
    await capture(page, '03-scheduled-jobs')
    await page.evaluate(() => {
      window.location.hash = '#/tasks'
    })
    await expect.poll(async () => page.evaluate(() => location.hash)).toBe('#/cron')
    await expect(scheduledJobsNav).toHaveAttribute('aria-current', 'page')
    await expect(scheduledJobs).toBeVisible()

    await nav.getByRole('button', { name: '新建对话', exact: true }).click()

    const composer = page
      .locator('[data-chat-surface]')
      .filter({ visible: true })
      .last()
      .locator('[contenteditable="true"]')
      .first()

    await composer.fill('请确认已打开默认配置档案所有的会话。')
    await composer.press('Enter')

    try {
      await expect(
        transcript.getByText('Hello from the mock inference server! The full boot chain is working.', { exact: true })
      ).toBeVisible({
        timeout: 60_000
      })
    } catch (error) {
      const latestBackendLog = path.join(sandbox.hermesHome, 'logs', 'latest.log')
      const profileHome = path.join(sandbox.hermesHome, 'profiles', 'research')
      const profileBackendLog = path.join(profileHome, 'logs', 'latest.log')

      const failureEvidence = {
        error: String(error),
        mockCompletions: mock.receivedCompletions.map(({ path: requestPath, body }) => ({
          path: requestPath,
          model: body.model,
          stream: body.stream
        })),
        mockPrompts: mock.receivedPrompts,
        backendLogs: [latestBackendLog, profileBackendLog].map(filePath => ({
          filePath,
          text: fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf8').slice(-20_000) : null
        }))
      }

      fs.writeFileSync(path.join(REPORT_ROOT, 'reply-failure.json'), JSON.stringify(failureEvidence, null, 2))
      throw error
    }

    await capture(page, '04-live-reply-in-owner-session')

    await page.evaluate(sessionId => {
      window.location.hash = `#/${encodeURIComponent(sessionId)}`
    }, OWNER_SESSION_ID)
    const ownerWorkspace = page.locator('[data-chat-surface][data-session-anchor="workspace"]')
    await expect(
      ownerWorkspace.locator('[data-slot="aui_thread-viewport"]').getByText(OWNER_MARKER, { exact: true })
    ).toBeVisible({ timeout: 45_000 })

    const initialOwnerSession = await page.evaluate(async sessionId => {
      const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

      return desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' })
    }, OWNER_SESSION_ID)

    expect(normalizedHostPath(initialOwnerSession.cwd)).toBe(normalizedHostPath(path.join(sandbox.root, 'project-A')))

    const ownerComposer = ownerWorkspace.locator('[data-slot="composer-rich-input"]')
    await expect(ownerComposer).toBeEditable()
    const chinesePayload = '你好，继续检查这个项目。'
    const completionsBeforeChineseInput = mock.receivedPrompts.length
    await ownerComposer.click()
    await page.keyboard.insertText(chinesePayload)
    await expect(ownerComposer).toContainText(chinesePayload)
    await expect(ownerWorkspace.locator('[data-slot="composer-root"] button[type="submit"]')).toHaveAttribute('aria-label', '发送')
    expect(mock.receivedPrompts).toHaveLength(completionsBeforeChineseInput)
    await capture(page, '05-chinese-text-entered-in-native-composer')
    await ownerComposer.press('Enter')
    await expect(ownerWorkspace.locator('[data-slot="aui_thread-viewport"]')).toContainText(chinesePayload, { timeout: 15_000 })
    await expect(ownerWorkspace.locator('[data-slot="aui_thread-viewport"]')).toContainText(MOCK_REPLY, { timeout: 60_000 })
    await expect(ownerComposer).toBeEmpty()
    await capture(page, '06-chinese-message-sent')

    await page.evaluate(paneStatesKey => {
      localStorage.setItem('hermes.desktop.rightContextOpen.v1', 'true')
      localStorage.setItem(paneStatesKey, JSON.stringify({ 'file-browser': { open: true } }))
    }, PANE_STATES_KEY)
    page.on('websocket', socket => {
      const websocketProfile = new URL(socket.url()).searchParams.get('profile') ?? 'default'

      socket.on('framesent', frame => {
        try {
          const payload = JSON.parse(String(frame.payload)) as unknown
          const requests = Array.isArray(payload) ? payload : [payload]

          for (const request of requests) {
            if (!request || typeof request !== 'object' || !('method' in request)) continue
            const method = String(request.method ?? '')
            const requestId = 'id' in request ? String(request.id ?? '') : ''
            const key = `${websocketProfile}:${requestId}`

            if (method && requestId) rpcMethodBySocketAndId.set(key, method)

            if (method === 'session.cwd.set' && 'params' in request && request.params && typeof request.params === 'object') {
              const params = request.params as { cwd?: unknown; session_id?: unknown }
              nativeProjectCwdCalls.push({ cwd: String(params.cwd ?? ''), sessionId: String(params.session_id ?? '') })
              nativeSessionRpcFrames.push({ profile: websocketProfile, method, params })
            } else if (['session.resume', 'session.activate'].includes(method) && 'params' in request) {
              nativeSessionRpcFrames.push({ profile: websocketProfile, method, params: request.params })
            }
          }
        } catch {
          // Ignore non-JSON and unrelated gateway frames.
        }
      })

      socket.on('framereceived', frame => {
        try {
          const payload = JSON.parse(String(frame.payload)) as unknown
          const responses = Array.isArray(payload) ? payload : [payload]

          for (const response of responses) {
            if (!response || typeof response !== 'object' || !('id' in response)) continue
            const requestId = String(response.id ?? '')
            const method = rpcMethodBySocketAndId.get(`${websocketProfile}:${requestId}`)

            if (method !== 'session.resume' && method !== 'session.activate') continue
            const result = 'result' in response ? response.result as Record<string, unknown> | undefined : undefined
            nativeSessionRpcFrames.push({
              profile: websocketProfile,
              method: `${method}.result`,
              runtimeSessionId: result?.session_id ?? null,
              storedSessionId: result?.session_key ?? null,
              cwd: (result?.info as Record<string, unknown> | undefined)?.cwd ?? null
            })
          }
        } catch {
          // Ignore non-JSON and unrelated gateway frames.
        }
      })
    })
    await page.reload()
    await expect(page.locator('[data-statusbar-item="gateway-health"]')).toContainText('就绪', { timeout: 60_000 })
    const filesPane = page.locator('aside[aria-label="右侧边栏"]')
    await expect(filesPane).toBeVisible()
    await expect(filesPane).toContainText('project-A')

    const initialOwnerCwd = await page.evaluate(async sessionId => {
      const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

      return (await desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' })).cwd
    }, OWNER_SESSION_ID)

    const productProjectButton = nav.getByRole('button', { name: '项目', exact: true })
    await productProjectButton.click()
    const productProjectBRow = nav.getByRole('button', { name: 'Project B', exact: true })
    await expect(productProjectBRow).toBeVisible({ timeout: 30_000 })
    await expect.poll(async () => normalizedHostPath((await readCwdObservation()).currentCwd)).toBe(
      normalizedHostPath(path.join(sandbox.root, 'project-A'))
    )
    const beforeProjectClick = await writeCwdDiagnostics('before-project-b-click')

    await productProjectBRow.click()
    await expect(productProjectBRow).toHaveAttribute('aria-expanded', 'true')
    await fs.promises.writeFile(
      path.join(REPORT_ROOT, 'session-rpc-frame-trace.json'),
      JSON.stringify(nativeSessionRpcFrames, null, 2)
    )
    await expect
      .poll(async () => {
        const detail = await page.evaluate(async sessionId => {
          const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

          return desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' })
        }, OWNER_SESSION_ID)

        return normalizedHostPath(detail.cwd)
      })
      .toBe(normalizedHostPath(path.join(sandbox.root, 'project-B')))

    expect(nativeProjectCwdCalls).toHaveLength(1)
    expect(nativeProjectCwdCalls[0]).toEqual({
      cwd: path.join(sandbox.root, 'project-B'),
      sessionId: expect.any(String)
    })
    expect(nativeProjectCwdCalls[0]?.sessionId).not.toBe(OWNER_SESSION_ID)
    const cwdResult = await writeCwdDiagnostics('after-project-b-click')
    fs.writeFileSync(path.join(REPORT_ROOT, 'product-nav-project-owner.json'), JSON.stringify({
      initialCwd: initialOwnerCwd,
      calls: nativeProjectCwdCalls,
      before: beforeProjectClick,
      after: cwdResult,
      finalCwd: cwdResult.observation.currentCwd
    }, null, 2))

    const projectBRow = nav.getByRole('button', { name: 'Project B', exact: true })
    await expect(projectBRow).toHaveAttribute('aria-expanded', 'true')
    await expect(page.locator('[data-chat-surface][data-session-anchor="workspace"]')).toBeVisible()

    const workspaceCwd = page.locator('[data-statusbar-item="workspace-cwd"]')
    await expect(workspaceCwd).toBeVisible()
    await expect(filesPane).toContainText('project-B', { timeout: 30_000 })

    const sessionDetail = await page.evaluate(async sessionId => {
      const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

      return desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' })
    }, OWNER_SESSION_ID)

    expect(normalizedHostPath(sessionDetail.cwd)).toBe(normalizedHostPath(path.join(sandbox.root, 'project-B')))
    await expect(page.locator('aside[aria-label="右侧边栏"]').getByText('project-B', { exact: true })).toBeVisible({
      timeout: 30_000
    })
    await capture(page, '05-project-b-selected-cwd')

    const cwdTranscript = ownerWorkspace.locator('[data-slot="aui_thread-viewport"]')
    const busyComposer = ownerWorkspace.locator('[contenteditable="true"]').first()
    await busyComposer.fill(`${CWD_BUSY_PROMPT} hold the turn so I can check workspace switching.`)
    await busyComposer.press('Enter')
    await expect(cwdTranscript).toContainText(CWD_BUSY_PROMPT, { timeout: 10_000 })
    await expect
      .poll(() => mock.receivedPrompts.some(prompt => prompt.includes(CWD_BUSY_PROMPT)), {
        message: 'busy turn should reach the isolated inference mock',
        timeout: 15_000
      })
      .toBe(true)
    await mock.waitForHeldStream()
    await expect(
      page.locator(
        '[data-chat-surface][data-session-anchor="workspace"] [data-slot="composer-root"] button[type="submit"]'
      )
    ).toHaveAttribute('aria-label', '停止', { timeout: 30_000 })
    const projectARow = nav.getByRole('button', { name: 'Project A', exact: true })
    await expect(projectARow).toBeVisible()
    await projectARow.click()
    const busyCwdNotice = page.locator('[data-slot="alert"]').filter({ hasText: '对话仍在执行当前任务' })
    await expect(busyCwdNotice).toBeVisible({ timeout: 10_000 })
    await expect.poll(() => collectErrorBanners(page)).toContain('对话仍在执行当前任务。请等本轮结束后，再选择项目。session busy')
    await capture(page, '06-busy-project-switch-rejected')
    await busyCwdNotice.getByRole('button', { name: '关闭通知', exact: true }).click()
    allowErrorBanners()
    expect(
      (
        await page.evaluate(async sessionId => {
          const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

          return desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' })
        }, OWNER_SESSION_ID)
      ).cwd
    ).toBe(path.join(sandbox.root, 'project-B'))
    await mock.releaseHeldStream()
    await expect(cwdTranscript).toContainText(CWD_BUSY_REPLY, { timeout: 30_000 })
    await expect(projectARow).toHaveAttribute('aria-expanded', 'false')

    await projectARow.click()
    await expect(projectARow).toHaveAttribute('aria-expanded', 'true')
    await expect(filesPane).toContainText('project-A', { timeout: 30_000 })

    const sessionAfterSwitchBack = await page.evaluate(async sessionId => {
      const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

      return desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' })
    }, OWNER_SESSION_ID)

    expect(sessionAfterSwitchBack.cwd).toBe(path.join(sandbox.root, 'project-A'))
    await capture(page, '07-project-a-selected-cwd')

    const projectBRowAfterStop = nav.getByRole('button', { name: 'Project B', exact: true })
    await projectBRowAfterStop.click()
    await expect(filesPane).toContainText('project-B', { timeout: 30_000 })

    const settledProjectBSession = await page.evaluate(async sessionId => {
      const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

      return desktop.api({ path: `/api/sessions/${sessionId}?profile=default`, profile: 'default' })
    }, OWNER_SESSION_ID)

    expect(settledProjectBSession.cwd).toBe(path.join(sandbox.root, 'project-B'))
    await capture(page, '08-project-b-selected-after-stop')

    await page.evaluate(sessionId => {
      window.location.hash = `#/${encodeURIComponent(sessionId)}`
    }, OWNER_SESSION_ID)
    await expect(
      page.locator('[data-chat-surface][data-session-anchor="workspace"] [data-slot="aui_thread-viewport"]')
        .getByText(OWNER_MARKER, { exact: true })
    ).toBeVisible({ timeout: 30_000 })
    const activeComposer = ownerWorkspace.locator('[contenteditable="true"]').first()

    await activeComposer.fill(BATCH_CLARIFY_TRIGGER)
    await activeComposer.press('Enter')

    const clarifyForm = page
      .locator('[data-chat-surface]')
      .filter({ visible: true })
      .last()
      .locator('form[data-clarify-batch]')

    await expect(clarifyForm).toBeVisible({ timeout: 60_000 })
    await expect(clarifyForm.getByText(BATCH_CLARIFY_QUESTIONS[0].question)).toBeVisible()
    await clarifyForm.getByRole('button', { name: /Coffee/ }).click()
    await clarifyForm.getByRole('button', { name: /Morning/ }).click()
    await clarifyForm.locator('button[type="submit"]').click()
    await expect(
      page.locator('[data-chat-surface]').filter({ visible: true }).last().locator('[data-clarify-settled]')
    ).toBeVisible({ timeout: 30_000 })
    await expect(cwdTranscript).toContainText(MOCK_REPLY, { timeout: 30_000 })
    await capture(page, '09-clarify-answered-in-conversation')

    await page.evaluate(() => {
      window.location.hash = '#/settings?tab=config%3Aappearance&setting=appearance.theme'
    })
    const appearance = page.getByRole('main').getByText('外观', { exact: true })
    await expect(appearance).toBeVisible({ timeout: 30_000 })
    const appearanceSettings = page.getByRole('main')
    const themeSearch = appearanceSettings.getByRole('textbox', { name: '搜索本地主题或 VS Code 市场…' })
    const searchField = themeSearch.locator('xpath=..')
    await expect.poll(() => searchField.evaluate(element => getComputedStyle(element).opacity)).toBe('0.6')
    await themeSearch.focus()
    await expect.poll(() => searchField.evaluate(element => getComputedStyle(element).opacity)).toBe('1')
    await themeSearch.blur()
    const mode = appearanceSettings.getByRole('group', { name: '主题' })

    const marketplaceHandlers = await app.evaluate(
      ({ ipcMain }, fixtures) => {
        type ThemeHandler = (event: unknown, value: string) => Promise<unknown>
        const handlers = (ipcMain as unknown as { _invokeHandlers?: Map<string, ThemeHandler> })._invokeHandlers

        if (!handlers) {return { available: false }}

        const searchKey = 'stardust:vscode-theme:search'
        const fetchKey = 'stardust:vscode-theme:fetch'

        handlers.set(searchKey, async (_event, query) =>
          query.toLowerCase().includes('stardust dusk') ? [fixtures.search] : []
        )
        handlers.set(fetchKey, async (_event, extensionId) =>
          extensionId === fixtures.theme.extensionId ? fixtures.theme : Promise.reject(new Error('Unknown fixture id.'))
        )

        return { available: true }
      },
      { search: MARKETPLACE_SEARCH_FIXTURE, theme: MARKETPLACE_THEME_FIXTURE }
    )

    expect(marketplaceHandlers.available).toBe(true)
    await themeSearch.fill('Stardust Dusk')
    const fixtureTheme = appearanceSettings.getByRole('button', { name: /Stardust Dusk Fixture/ })
    await expect(fixtureTheme).toBeVisible({ timeout: 15_000 })
    await fixtureTheme.click()
    const importedThemeName = 'vsc-stardust-dusk-fixture'
    await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', importedThemeName)
    await themeSearch.fill('')
    await expect(appearanceSettings.getByRole('button', { name: 'Stardust Dusk Fixture VS Code' })).toBeVisible()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('hermes-desktop-user-themes-v1')))
      .toContain(importedThemeName)

    const installedThemeStorage = await page.evaluate(() =>
      JSON.parse(localStorage.getItem('hermes-desktop-user-themes-v1') ?? '{}')
    )

    expect(installedThemeStorage[importedThemeName].description).toContain('stardust.fixture-dusk')
    expect(installedThemeStorage[importedThemeName].colors.background).toBe('#f9fafb')
    expect(installedThemeStorage[importedThemeName].darkColors.background).toBe('#171923')
    await mode.getByRole('button', { name: '暗色' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-hermes-mode', 'dark')
    await expect.poll(() => page.locator('html').evaluate(element => getComputedStyle(element).getPropertyValue('--theme-background-seed').trim())).toBe('#171923')
    await capture(page, '05-marketplace-theme-imported-and-dark-mode')
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', importedThemeName)
    await themeSearch.fill('')
    await expect(appearanceSettings.getByRole('button', { name: 'Stardust Dusk Fixture VS Code' })).toBeVisible({ timeout: 30_000 })
    await appearanceSettings.getByRole('button', { name: '移除主题' }).click()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('hermes-desktop-user-themes-v1')))
      .not.toContain(importedThemeName)
    await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
    await expect.poll(() => page.evaluate(() => localStorage.getItem('hermes-desktop-user-themes-v1'))).toBe('{}')
    await mode.getByRole('button', { name: '暗色' }).click()
    await mode.getByRole('button', { name: '明亮' }).focus()
    await page.keyboard.press('ArrowRight')
    await expect(mode.getByRole('button', { name: '暗色' })).toBeFocused()
    const nousCard = appearanceSettings.getByRole('button', { name: /Nous GitHub chrome, Nous blue accent/ })
    await nousCard.click()
    await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
    await expect(page.locator('html')).toHaveAttribute('data-hermes-mode', 'dark')
    await capture(page, '05-settings-dark-nous-theme')
    await page.getByRole('button', { name: '关闭设置' }).click()

    await nav.getByRole('button', { name: '工具', exact: true }).click()
    await expect(page.getByRole('heading', { name: '澄清问题', level: 3 })).toBeVisible({ timeout: 30_000 })
    await capture(page, '06-tools-page')
    await nav.getByRole('button', { name: '消息渠道', exact: true }).click()
    const messagingHeading = page.getByRole('heading', { name: '消息渠道' })
    await expect(messagingHeading).toBeVisible({ timeout: 30_000 })
    const messagingPage = messagingHeading.locator('xpath=ancestor::section')
    const messagingMain = messagingPage.locator('[data-md-split="rail"]')
    const profileResearch = messagingPage.getByRole('button', { name: 'research', exact: true })
    const telegramRow = messagingMain.getByRole('button', { name: /Telegram/ })
    const whatsappRow = messagingMain.getByRole('button', { name: /WhatsApp/ }).first()
    await expect(profileResearch).toBeVisible()
    await expect(telegramRow).toBeVisible()
    await expect(whatsappRow).toBeVisible()
    await profileResearch.click()
    const researchScopeNotice = messagingPage.locator('[data-scope-loud="true"]')
    await expect(researchScopeNotice).toContainText('research', { timeout: 30_000 })
    await expect(profileResearch).toHaveClass(/bg-\(--ui-bg-tertiary\)/)
    await expect(messagingMain.getByRole('button', { name: /Telegram/ })).toBeVisible({ timeout: 30_000 })

    const messagingRequestScopes = await app.evaluate(
      () =>
        (globalThis as typeof globalThis & { __taskOwnerRequestScopes?: Array<{ path?: string; profile?: string }> })
          .__taskOwnerRequestScopes ?? []
    )

    expect(messagingRequestScopes).toContainEqual(
      expect.objectContaining({ path: '/api/messaging/platforms', profile: 'research' })
    )
    await expect(messagingMain.getByRole('button', { name: /WhatsApp/ }).first()).toBeVisible()
    await capture(page, '07-messaging-page')

    await nav.getByRole('button', { name: '智能体空间', exact: true }).click()
    const agentSpace = page.locator('[data-slot-page="agents-home"]')
    await expect(agentSpace).toBeVisible({ timeout: 30_000 })
    await expect(nav.getByRole('button', { name: '新建对话', exact: true })).toBeVisible()
    await expect(agentSpace.getByRole('heading', { name: '认识你的 AI 伙伴' })).toBeVisible()
    const mainAssistantCard = agentSpace.locator('[data-main-assistant-card]')
    await expect(mainAssistantCard).toBeVisible()
    await expect(mainAssistantCard).toContainText('你的主助手')
    await expect(mainAssistantCard).toContainText('Stardust')
    const partnersSection = agentSpace.getByRole('region', { name: '你的伙伴' })
    await expect(partnersSection).toContainText('1 位伙伴')
    await expect(partnersSection).toHaveAttribute('aria-label', '你的伙伴')
    await expect(partnersSection.locator('[data-agent-card]')).toHaveCount(1)
    const researchAgent = agentSpace.locator('[data-agent-card="research"]')
    await expect(researchAgent).toBeVisible()
    await expect(researchAgent).toContainText('研究伙伴')
    await expect(researchAgent).toContainText('协助梳理资料、比较证据并总结发现。')
    await expect(researchAgent).toContainText('现在可以联系它')
    await capture(page, '08-agent-space-research-partner')
    await mainAssistantCard.getByRole('button', { name: '与 Stardust 对话', exact: true }).click()
    await expect(agentSpace).toBeHidden({ timeout: 30_000 })
    await expect(page.locator('[data-chat-surface]').filter({ visible: true }).last()).toBeVisible({ timeout: 30_000 })
    await nav.getByRole('button', { name: '智能体空间', exact: true }).click()
    await expect(agentSpace).toBeVisible({ timeout: 30_000 })
    await agentSpace.getByRole('button', { name: '管理伙伴', exact: true }).click()
    const agentManager = page.locator('[data-slot-page="agents-manager"]')
    await expect(agentManager).toBeVisible({ timeout: 15_000 })
    const researchAgentRow = agentManager.locator('[data-roster-key="local::research"]')
    const defaultRow = agentManager.locator('[data-roster-key="local::default"]')
    await expect(researchAgentRow).toBeVisible()
    await expect(defaultRow).toBeVisible()
    await defaultRow.click({ button: 'right' })
    const defaultContextMenu = page.locator('[data-slot="context-menu-content"]:visible')
    await expect(defaultContextMenu).toBeVisible({ timeout: 5_000 })
    const defaultMenuLabels = await defaultContextMenu.locator('[role="menuitem"]').allTextContents()
    expect(defaultMenuLabels.some(label => label.includes('删除'))).toBe(false)
    await page.keyboard.press('Escape')
    await expect(agentManager.locator('[data-roster-key]')).toHaveCount(2)
    await expect.poll(() => agentManager.getByRole('textbox').count()).toBe(0)
    await researchAgentRow.click({ button: 'right' })
    const rosterContextMenu = page.locator('[data-slot="context-menu-content"]:visible')
    await expect(rosterContextMenu).toBeVisible({ timeout: 5_000 })
    const rosterMenuLabels = await rosterContextMenu.locator('[role="menuitem"]').allTextContents()
    expect(rosterMenuLabels).toContain('编辑…')
    await rosterContextMenu.getByText('编辑…', { exact: true }).click()
    const editProfile = page.getByRole('dialog')
    await expect(editProfile.getByRole('heading', { name: '编辑配置档案' })).toBeVisible()
    const roleDescription = editProfile.locator('textarea')
    await expect(roleDescription).toHaveValue('协助梳理资料、比较证据并总结发现。')
    await roleDescription.fill('整理研究资料，比较证据并形成清晰结论。')
    await editProfile.getByRole('button', { name: '保存' }).click()
    await expect(editProfile).toBeHidden({ timeout: 15_000 })
    await expect
      .poll(async () => {
        const result = await page.evaluate(async () => {
          const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

          return desktop.api({ path: '/api/profiles', profile: 'default' })
        })

        return result.profiles?.find((profile: { name: string }) => profile.name === 'research')?.description
      })
      .toBe('整理研究资料，比较证据并形成清晰结论。')
    await capture(page, '09-agent-manager-role-saved')
    await agentManager.getByRole('button', { name: '返回我的伙伴', exact: true }).click()
    await expect(agentSpace).toBeVisible({ timeout: 15_000 })
    await expect(researchAgent).toContainText('整理研究资料，比较证据并形成清晰结论。')
    await researchAgent.getByRole('button', { name: '与研究伙伴开始交流', exact: true }).click()
    await expect(agentSpace).toBeHidden({ timeout: 30_000 })
    const researchBotWorkspace = page.locator('[data-chat-surface][data-session-anchor="workspace"]').filter({ visible: true }).last()
    await expect(researchBotWorkspace).toBeVisible({ timeout: 30_000 })
    await expect(researchBotWorkspace.locator('[data-slot="aui_thread-viewport"]')).toContainText('研究伙伴')
    await capture(page, '09-research-partner-canonical-chat')

    await nav.getByRole('button', { name: '智能体空间', exact: true }).click()
    await expect(agentSpace).toBeVisible({ timeout: 30_000 })
    const deleteResearchPartner = agentSpace.getByRole('button', { name: '删除研究伙伴', exact: true })
    await expect(deleteResearchPartner).toBeVisible()
    await deleteResearchPartner.click()
    const deletePartnerDialog = page.getByRole('dialog')
    await expect(deletePartnerDialog.getByRole('heading', { name: '删除研究伙伴？' })).toBeVisible()
    await expect(deletePartnerDialog).toContainText('配置档案、设置、记忆、对话、技能和定时任务')
    await capture(page, '10-delete-partner-confirm')
    await deletePartnerDialog.getByRole('button', { name: '删除伙伴', exact: true }).click()
    await expect(deletePartnerDialog).toBeHidden({ timeout: 15_000 })
    await expect
      .poll(async () => {
        const result = await page.evaluate(async () => {
          const desktop = (window as unknown as { stardustDesktop: any }).stardustDesktop

          return desktop.api({ path: '/api/profiles', profile: 'default' })
        })

        return result.profiles?.some((profile: { name: string }) => profile.name === 'research')
      })
      .toBe(false)
    await expect(agentSpace.locator('[data-agent-card="research"]')).toHaveCount(0)

    await nav.getByRole('button', { name: '连接器', exact: true }).click()
    await expect(page.getByRole('heading', { name: '连接器', level: 1 })).toBeVisible({ timeout: 30_000 })
    await capture(page, '08-connectors-page')

    await page.getByRole('button', { name: '打开设置' }).click()
    await expect(page.getByRole('button', { name: '关闭设置' })).toBeVisible()
    await page.keyboard.press('Escape')
    await page.evaluate(() => {
      window.location.hash = '#/tasks'
    })
    await expect.poll(async () => page.evaluate(() => location.hash)).toBe('#/cron')
    await expect(scheduledJobs).toBeVisible()
    const viewportBefore = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }))
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(860, 640))
    await expect.poll(async () => page.evaluate(() => innerWidth)).toBeLessThan(viewportBefore.width)
    await expect(scheduledJobs).toBeVisible()
    await expect
      .poll(async () =>
        page.locator('[data-cron-page] [class*="@container/panel-body"]').evaluate(element => {
          const body = element.firstElementChild as HTMLElement | null
          const list = body?.firstElementChild as HTMLElement | null
          const detail = body?.lastElementChild as HTMLElement | null
          const rect = detail?.getBoundingClientRect()
          const styles = body ? getComputedStyle(body) : null

          return {
            container: Math.round(element.getBoundingClientRect().width),
            detail: Math.round(rect?.width ?? 0),
            direction: styles?.flexDirection,
            heading: Math.round(detail?.querySelector('h2, h3')?.getBoundingClientRect().width ?? 0)
          }
        })
      )
      .toMatchObject({ direction: 'column' })

    const resizedPanel = await page
      .locator('[data-cron-page] [class*="@container/panel-body"]')
      .evaluate(element => {
        const body = element.firstElementChild as HTMLElement
        const detail = body.lastElementChild as HTMLElement

        return { containerWidth: element.getBoundingClientRect().width, detailWidth: detail.getBoundingClientRect().width }
      })

    expect(resizedPanel.detailWidth).toBeGreaterThan(resizedPanel.containerWidth * 0.9)
    await capture(page, 'panel-responsive-860')

    const narrowScrollWidth = await scheduledJobs.evaluate(element => element.scrollWidth)
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(600, 640))
    await expect.poll(async () => page.evaluate(() => innerWidth)).toBeLessThan(640)
    await expect
      .poll(async () =>
        page.locator('[data-cron-page] [class*="@container/panel-body"]').evaluate(element => {
          const body = element.firstElementChild as HTMLElement | null

          return {
            container: Math.round(element.getBoundingClientRect().width),
            direction: body ? getComputedStyle(body).flexDirection : null
          }
        })
      )
      .toMatchObject({ direction: 'column' })

    const narrowOverflow = await page.evaluate(() => ({
      viewportWidth: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth
    }))

    expect(narrowOverflow.documentWidth).toBeLessThanOrEqual(narrowOverflow.viewportWidth)

    // Keep the full detail column visible without horizontal scrolling.
    const narrowPanel = await page
      .locator('[data-cron-page] [class*="@container/panel-body"]')
      .evaluate(element => {
        const detail = element.firstElementChild?.lastElementChild as HTMLElement | null

        return { containerWidth: element.getBoundingClientRect().width, detailWidth: detail?.getBoundingClientRect().width ?? 0 }
      })

    expect(narrowPanel.detailWidth).toBeGreaterThan(narrowPanel.containerWidth * 0.9)
    await capture(page, 'panel-responsive-600')

    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1520, 800))
    await expect.poll(async () => page.evaluate(() => innerWidth)).toBeGreaterThanOrEqual(1400)
    await expect
      .poll(async () =>
        page.locator('[data-cron-page] [class*="@container/panel-body"]').evaluate(element => {
          const body = element.firstElementChild as HTMLElement | null

          return body ? getComputedStyle(body).flexDirection : null
        })
      )
      .toBe('row')
    expect(await collectErrorBanners(page)).toContain('对话仍在执行当前任务。请等本轮结束后，再选择项目。session busy')
    fs.writeFileSync(
      path.join(REPORT_ROOT, 'interaction-checklist.json'),
      JSON.stringify(
        [
          'chat home and all-profile navigation',
          'same stored id appears first in the non-owner research profile',
          'same-id profile selection paints only the selected owner row in both directions',
          'scheduled jobs opens directly and supports create then cancel',
          'legacy tasks route redirects to scheduled jobs',
          'same-id profile selection paints only the selected owner row in both directions',
          'same-id profile selection paints only the selected owner row in both directions',
          'profile-qualified session selection opens the intended transcript',
          'real prompt/reply stays on the routed session',
          'Files root and persisted owner-session cwd switch between Project A and Project B',
          'busy Project B turn rejects a Project A switch without changing cwd',
          'the Project A switch succeeds after the held turn settles',
          'the owner session runs pwd in Project B and persists the tool result',
          'batch clarify questions accept answers and settle in the active conversation',
          'Chinese text entered into the native Electron composer and delivered through the real mock-backed conversation; IME candidate selection was not asserted',
          'Marketplace appearance search and import through renderer preload IPC, fixture response, theme conversion, light/dark activation, persistence after reload, and UI removal',
          'Agent Space separates the Stardust main assistant, opens its main chat, keeps the primary profile non-deletable, edits a partner role, opens its canonical chat, and permanently deletes that partner after confirmation',
          'team capacity counts secondary partners without counting the Stardust main assistant',
          'Tools, Messaging, Connectors and profile-scoped requests',
          'scheduled-job detail reflows at 860px and fills the panel width',
          'scheduled-job detail reflows again below the 640px dock breakpoint without horizontal overflow',
          'wide panel restores the side-by-side list and detail layout',
          'window resize preserves scheduled jobs'
        ],
        null,
        2
      )
    )
  })
})
