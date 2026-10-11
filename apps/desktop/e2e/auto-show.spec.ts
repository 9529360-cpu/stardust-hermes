/**
 * Auto-show of generated files, in the real window.
 *
 * The mocked model calls the real write_file and desktop_preview tools, and the
 * desktop decides what opens in the right-rail preview (src/lib/auto-show.ts):
 *   A. a file the assistant writes opens once while its turn is still running;
 *   B. a second write of that file in the same turn does not reopen it;
 *   C. with the Settings switch off, the write does not open the rail, and an
 *      explicit preview.open still does;
 *   D. a file outside the session workspace opens by neither path;
 *   E. at a 560 px window, an auto-opened file does not open the narrow overlay.
 *
 * Each case launches its own app with its own workspace, so tabs never carry
 * over. Checks read the persisted tab list as well as the DOM, because a
 * collapsed rail can hide a tab that was still opened. AUTO_SHOW_SCREENSHOT_DIR=<dir>
 * saves captures for review; they are never part of the assertions.
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { receivedUserTexts, type MockServer, type ScriptedTurn } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { type ElectronApplication, expect, type Page, test } from './test'

const PREVIEW_TABS_KEY = 'hermes.desktop.previewTabs.v2'
const RIGHT_CONTEXT_KEY = 'hermes.desktop.rightContextOpen.v1'

/**
 * The first message of a fresh app waits in the composer until the agent finishes building
 * (tool discovery, model setup). On a cold start that build can take minutes, so each turn's
 * own budgets start only once the mock has received the message.
 */
const AGENT_START_TIMEOUT_MS = 240_000
/** Whole-case budget: app launch, agent start, then the turn itself. */
const CASE_TIMEOUT_MS = 540_000

function pageHtml(title: string): string {
  return `<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8"><title>${title}</title></head>\n<body><h1>${title}</h1></body>\n</html>\n`
}

function makeTempDir(label: string): string {
  // realpath: the backend reports resolved paths, and containment compares strings.
  return fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), `auto-show-${label}-`)))
}

async function removeDir(dir: string): Promise<void> {
  await fs.promises.rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The right-rail tab for a file. A tab's label is the file's basename. */
function railTab(page: Page, fileName: string) {
  return page.getByRole('tab', { name: new RegExp(escapeRegExp(fileName)) })
}

function transcript(page: Page) {
  return page.locator('[data-slot="aui_thread-viewport"]')
}

/** How many persisted rail tabs point at this file. The key is rewritten on every tab change. */
async function persistedTabs(page: Page, fileName: string): Promise<number> {
  return page.evaluate(
    ({ key, name }) => {
      const tabs: unknown[] = JSON.parse(window.localStorage.getItem(key) ?? '[]')

      return tabs.filter(tab => JSON.stringify(tab).includes(name)).length
    },
    { key: PREVIEW_TABS_KEY, name: fileName },
  )
}

async function send(page: Page, text: string): Promise<void> {
  const composer = page.locator('[contenteditable="true"]').first()

  await composer.waitFor({ state: 'visible', timeout: 15_000 })
  await composer.click()
  await composer.fill(text)
  await page.keyboard.press('Enter')
}

/**
 * Send a scripted message, then wait until the mock has received it. The first word is the
 * scripted trigger. A cold agent build can outlast a turn's own budget, so the turn is timed
 * from the moment the agent starts it, not from the send.
 */
async function sendScripted(page: Page, text: string): Promise<void> {
  const trigger = text.split(' ')[0]

  await send(page, text)
  await expect
    .poll(() => receivedUserTexts().some(received => received.includes(trigger)), {
      message: `The agent never started the turn ${trigger} within ${AGENT_START_TIMEOUT_MS / 1000} s`,
      timeout: AGENT_START_TIMEOUT_MS,
    })
    .toBe(true)
}

async function closeRailTab(page: Page, fileName: string): Promise<void> {
  const tab = railTab(page, fileName)

  await tab.hover()
  await tab.getByRole('button', { name: /^(关闭|Close)$/ }).click()
}

/** Settings → Appearance → "Show generated files automatically". Returns to the chat afterwards. */
async function setAutoShowSwitch(page: Page, on: boolean): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = '#/settings?tab=config%3Aappearance'
  })

  const row = page
    .getByText('自动展示生成的文件', { exact: true })
    .or(page.getByText('Show generated files automatically', { exact: true }))
    .locator('xpath=ancestor::div[.//button[@aria-pressed]][1]')

  await row.getByRole('button', { name: on ? /^(开|On)$/ : /^(关|Off)$/ }).click()
  await expect(row.locator('button[aria-pressed="true"]')).toHaveText(on ? /^(开|On)$/ : /^(关|Off)$/)

  await page.evaluate(() => {
    window.location.hash = '#/'
  })
  await expect(page.locator('[contenteditable="true"]').first()).toBeVisible({ timeout: 15_000 })
}

async function setWindowSize(app: ElectronApplication, page: Page, width: number, height: number): Promise<void> {
  await app.evaluate(
    ({ BrowserWindow }, size) => {
      BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
    },
    { width, height },
  )
  // The breakpoint is a media query; give the layout a frame to re-evaluate.
  await page.waitForTimeout(700)
  expect(await page.evaluate(() => window.innerWidth)).toBe(width)
}

/** Wait until the mock holds a model call. Fails with a clear message if the turn never gets there. */
async function untilHeld(mock: MockServer, what: string): Promise<void> {
  let timer: NodeJS.Timeout | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`The turn never reached ${what} within 90 s`)), 90_000)
  })

  try {
    await Promise.race([mock.waitForHeldCompletion(), timeout])
  } finally {
    clearTimeout(timer)
  }
}

// Screenshot captures are for review only; they are never assertions.
async function capture(page: Page, name: string): Promise<void> {
  const dir = process.env.AUTO_SHOW_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

interface CaseContext {
  fixture: MockBackendFixture
  workspace: string
}

interface CaseSetup {
  /** Scripted turns for the mock, built from the session workspace path. */
  scripts: (workspace: string) => Record<string, ScriptedTurn[]>
  /** The first model call whose request mentions this text is held until released. */
  holdFirstCompletionContaining?: string
  /** Files written into the workspace before the app starts. */
  seed?: Record<string, string>
}

/**
 * Launch one app against a fresh workspace (the session cwd, via terminal.cwd),
 * run the body, then shut everything down. The workspace is removed afterwards.
 */
async function runCase(label: string, setup: CaseSetup, body: (ctx: CaseContext) => Promise<void>): Promise<void> {
  const workspace = makeTempDir(label)

  try {
    for (const [name, content] of Object.entries(setup.seed ?? {})) {
      fs.writeFileSync(path.join(workspace, name), content, 'utf8')
    }

    const fixture = await setupMockBackend({
      mockServer: {
        autoShowScripts: setup.scripts(workspace),
        holdFirstCompletionContaining: setup.holdFirstCompletionContaining,
      },
      extraConfig: `terminal:\n  cwd: ${JSON.stringify(workspace)}`,
    })

    try {
      await waitForAppReady(fixture, 120_000)
      await body({ fixture, workspace })
    } finally {
      await fixture.cleanup()
    }
  } finally {
    await removeDir(workspace)
  }
}

test('A: a file written in a live turn opens once in the rail while the turn is still running', async () => {
  test.setTimeout(CASE_TIMEOUT_MS)

  await runCase(
    'a',
    {
      scripts: workspace => ({
        'write-page': [
          {
            text: 'Writing the page now.',
            toolCalls: [{ name: 'write_file', args: { path: path.join(workspace, 'auto-show-a.html'), content: pageHtml('Auto-show case A') } }],
          },
          { text: 'The page is ready.' },
        ],
      }),
      // The second model call is held, so the turn is still live when the tab is checked.
      holdFirstCompletionContaining: 'auto-show-a.html',
    },
    async ({ fixture, workspace }) => {
      const { page, mock } = fixture
      const file = path.join(workspace, 'auto-show-a.html')

      await sendScripted(page, 'E2E_AUTO_SHOW(write-page) make a page for me')
      await untilHeld(mock, 'the reply after the write')

      await expect.poll(() => persistedTabs(page, 'auto-show-a.html')).toBe(1)
      await expect(railTab(page, 'auto-show-a.html')).toBeVisible()
      await expect(transcript(page)).not.toContainText('The page is ready.')
      await capture(page, 'a-live-turn-auto-open')

      mock.releaseHeldStream()
      await expect(transcript(page)).toContainText('The page is ready.', { timeout: 60_000 })
      expect(fs.readFileSync(file, 'utf8')).toContain('Auto-show case A')

      await page.waitForTimeout(1500)
      await expect(railTab(page, 'auto-show-a.html')).toHaveCount(1)
      expect(await persistedTabs(page, 'auto-show-a.html')).toBe(1)
    },
  )
})

test('B: a second write of the same file in the same turn does not reopen it', async () => {
  test.setTimeout(CASE_TIMEOUT_MS)

  await runCase(
    'b',
    {
      scripts: workspace => ({
        'write-twice': [
          {
            text: 'Writing the page.',
            toolCalls: [{ name: 'write_file', args: { path: path.join(workspace, 'auto-show-b.html'), content: pageHtml('version 1') } }],
          },
          {
            text: 'Updating the page.',
            toolCalls: [{ name: 'write_file', args: { path: path.join(workspace, 'auto-show-b.html'), content: pageHtml('version 2') } }],
          },
          { text: 'Both writes are done.' },
        ],
      }),
      holdFirstCompletionContaining: 'auto-show-b.html',
    },
    async ({ fixture, workspace }) => {
      const { page, mock } = fixture

      await sendScripted(page, 'E2E_AUTO_SHOW(write-twice) write the page, then update it')
      // The first write has landed and opened the file. Its follow-up reply is held.
      await untilHeld(mock, 'the reply after the first write')
      await expect.poll(() => persistedTabs(page, 'auto-show-b.html')).toBe(1)
      await expect(railTab(page, 'auto-show-b.html')).toBeVisible()

      // The user closes the tab. The rewrite must not bring it back in this turn.
      await closeRailTab(page, 'auto-show-b.html')
      await expect.poll(() => persistedTabs(page, 'auto-show-b.html')).toBe(0)

      mock.releaseHeldStream()
      await expect(transcript(page)).toContainText('Both writes are done.', { timeout: 60_000 })
      // Auto-open is asynchronous (the file is resolved first), so give a late reopen time to show.
      await page.waitForTimeout(2000)

      expect(await persistedTabs(page, 'auto-show-b.html')).toBe(0)
      await expect(railTab(page, 'auto-show-b.html')).toHaveCount(0)
      expect(fs.readFileSync(path.join(workspace, 'auto-show-b.html'), 'utf8')).toContain('version 2')
    },
  )
})

test('C: with the switch off a write does not open the rail, and an explicit preview still does', async () => {
  test.setTimeout(CASE_TIMEOUT_MS)

  await runCase(
    'c',
    {
      scripts: workspace => ({
        'write-then-open': [
          {
            text: 'Writing the notes.',
            toolCalls: [{ name: 'write_file', args: { path: path.join(workspace, 'auto-show-c.html'), content: pageHtml('Auto-show case C') } }],
          },
          {
            text: 'Opening the spec sheet.',
            toolCalls: [{ name: 'desktop_preview', args: { action: 'open', url: path.join(workspace, 'auto-show-c-spec.html') } }],
          },
          { text: 'Both steps are done.' },
        ],
      }),
      // The reply after the write is held: the write has finished, the explicit open has not.
      holdFirstCompletionContaining: 'auto-show-c.html',
      seed: { 'auto-show-c-spec.html': pageHtml('Spec sheet') },
    },
    async ({ fixture }) => {
      const { page, mock } = fixture

      await setAutoShowSwitch(page, false)
      await sendScripted(page, 'E2E_AUTO_SHOW(write-then-open) write the notes, then show the spec')
      await untilHeld(mock, 'the reply after the write')

      await page.waitForTimeout(2000)
      expect(await persistedTabs(page, 'auto-show-c.html')).toBe(0)
      expect(await page.evaluate(key => window.localStorage.getItem(key), RIGHT_CONTEXT_KEY)).not.toBe('true')
      await capture(page, 'c-switch-off-after-write')

      mock.releaseHeldStream()
      await expect.poll(() => persistedTabs(page, 'auto-show-c-spec.html')).toBe(1)
      await expect(railTab(page, 'auto-show-c-spec.html')).toBeVisible()
      await expect(transcript(page)).toContainText('Both steps are done.', { timeout: 60_000 })
      await page.waitForTimeout(1000)
      expect(await persistedTabs(page, 'auto-show-c.html')).toBe(0)
      await capture(page, 'c-explicit-open-still-opens')
    },
  )
})

test('D: a file outside the session workspace opens by neither path', async () => {
  test.setTimeout(CASE_TIMEOUT_MS)
  const outsideDir = makeTempDir('outside')
  const outside = path.join(outsideDir, 'auto-show-outside.html')

  try {
    await runCase(
      'd',
      {
        scripts: workspace => ({
          outside: [
            {
              text: 'Writing a file outside the project.',
              toolCalls: [{ name: 'write_file', args: { path: outside, content: pageHtml('Outside') } }],
            },
            {
              text: 'Showing the outside file.',
              toolCalls: [{ name: 'desktop_preview', args: { action: 'open', url: outside } }],
            },
            {
              text: 'Showing the spec sheet.',
              toolCalls: [{ name: 'desktop_preview', args: { action: 'open', url: path.join(workspace, 'auto-show-d-spec.html') } }],
            },
            { text: 'Outside checks are done.' },
          ],
        }),
        holdFirstCompletionContaining: 'auto-show-outside.html',
        seed: { 'auto-show-d-spec.html': pageHtml('Spec sheet') },
      },
      async ({ fixture }) => {
        const { page, mock } = fixture

        await sendScripted(page, 'E2E_AUTO_SHOW(outside) write outside the project, then show things')
        await untilHeld(mock, 'the reply after the outside write')

        // The write really happened outside the workspace, and nothing opened.
        expect(fs.existsSync(outside)).toBe(true)
        await page.waitForTimeout(2000)
        expect(await persistedTabs(page, 'auto-show-outside.html')).toBe(0)

        mock.releaseHeldStream()
        // Positive control: the in-workspace explicit open still lands after the outside one was refused.
        await expect.poll(() => persistedTabs(page, 'auto-show-d-spec.html')).toBe(1)
        await expect(transcript(page)).toContainText('Outside checks are done.', { timeout: 60_000 })
        await page.waitForTimeout(1000)
        expect(await persistedTabs(page, 'auto-show-outside.html')).toBe(0)
        await expect(railTab(page, 'auto-show-outside.html')).toHaveCount(0)
      },
    )
  } finally {
    await removeDir(outsideDir)
  }
})

test('E: at 560 px an auto-opened file does not open the narrow overlay', async () => {
  test.setTimeout(CASE_TIMEOUT_MS)

  await runCase(
    'e',
    {
      scripts: workspace => ({
        'narrow-write': [
          {
            text: 'Writing the narrow page.',
            toolCalls: [{ name: 'write_file', args: { path: path.join(workspace, 'auto-show-e.html'), content: pageHtml('Narrow') } }],
          },
          { text: 'Narrow page is ready.' },
        ],
      }),
    },
    async ({ fixture }) => {
      const { app, page } = fixture

      await setWindowSize(app, page, 560, 820)
      await sendScripted(page, 'E2E_AUTO_SHOW(narrow-write) make a narrow page')
      await expect(transcript(page)).toContainText('Narrow page is ready.', { timeout: 60_000 })

      // The auto-open really ran: the file is a persisted rail tab.
      await expect.poll(() => persistedTabs(page, 'auto-show-e.html')).toBe(1)
      await page.waitForTimeout(1000)

      // The element checked: the narrow overlay root, which only renders on an explicit reveal.
      await expect(page.locator('[data-narrow-overlay]')).toHaveCount(0)

      const tab = railTab(page, 'auto-show-e.html')
      test.info().annotations.push({
        type: 'observation',
        description: `560 px: [data-narrow-overlay] count 0; rail tab in DOM ${await tab.count()}, visible ${await tab.isVisible()}`,
      })
      await capture(page, 'e-narrow-560')
    },
  )
})
