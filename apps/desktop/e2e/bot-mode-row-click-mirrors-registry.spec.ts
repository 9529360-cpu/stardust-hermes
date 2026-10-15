import fs from 'node:fs'
import path from 'node:path'

import { MOCK_REPLY, startMockServer } from '../../../tests-js/scripts/mock-server'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  type MockBackendFixture,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { RealSessionBuilder } from './real-session-builder'
import { expect, test } from './test'

// A bot row previews the bot's canonical Bot Chat (the gateway resolves it by
// name on every roster poll). Clicking the row must land on THAT conversation.
// Before this fix a plain click fronted whatever bots-workspace tile the user
// last had open for that bot — a `+` side thread outlived every restart in
// Local Storage and won every click forever, while the row kept previewing the
// Bot Chat. The user saw the sidebar and the center describe two different
// conversations ("sessions not in sync"; support thread 1544460286084391043).

type Page = MockBackendFixture['page']

let fixture: MockBackendFixture | null = null

async function openBots(page: Page): Promise<void> {
  const tab = page.getByRole('button', { name: /智能体空间|Agent space/ }).first()

  await tab.click()
  await expect(page.getByRole('button', { name: 'New bot or group chat' })).toBeVisible()
}

async function settle(page: Page, timeout = 90_000): Promise<void> {
  await page
    .getByText(/Waking up/i)
    .first()
    .waitFor({ state: 'hidden', timeout })
    .catch(() => undefined)
  await page.waitForTimeout(500)
}

const roster = (page: Page) => page.locator('[data-slot="bots-roster"]')
const botRow = (page: Page, name: string) => roster(page).locator(`[data-roster-key="local::${name}"]`)

// Diagnostics for the roster flake: record every roster-row mount and unmount in
// the page, and print the tail only when this test fails. Assertions are unchanged.
async function watchRosterChurn(page: Page): Promise<void> {
  await page.evaluate(() => {
    const win = window as unknown as { __rosterChurn?: string[] }
    const started = performance.now()

    win.__rosterChurn = []

    const rosterKey = (node: Node): string | null => {
      if (!(node instanceof HTMLElement)) {
        return null
      }

      return (
        node.getAttribute('data-roster-key') ??
        node.querySelector('[data-roster-key]')?.getAttribute('data-roster-key') ??
        null
      )
    }

    const record = (op: string, node: Node) => {
      const key = rosterKey(node)

      if (key) {
        win.__rosterChurn?.push(`${Math.round(performance.now() - started)}ms ${op} ${key}`)
      }
    }

    new MutationObserver(records => {
      for (const change of records) {
        change.removedNodes.forEach(node => record('removed', node))
        change.addedNodes.forEach(node => record('added', node))
      }
    }).observe(document.body, { childList: true, subtree: true })

    // Sample the roster root, its row count, and the "Waking up" state, so the dump
    // shows which pane branch the rows disappear into, even if a mutation is missed.
    let lastState = ''

    window.setInterval(() => {
      const root = document.querySelector('[data-slot="bots-roster"]')
      const rows = root ? root.querySelectorAll('[data-roster-key]').length : -1
      const waking = /Waking up/i.test(document.body.innerText)
      const state = `root=${root ? 1 : 0} rows=${rows} waking=${waking ? 1 : 0}`

      if (state !== lastState) {
        lastState = state
        win.__rosterChurn?.push(`${Math.round(performance.now() - started)}ms sample ${state}`)
      }
    }, 250)
  })
}

async function dumpRosterChurn(page: Page, label: string): Promise<void> {
  const events = await page.evaluate(() => (window as unknown as { __rosterChurn?: string[] }).__rosterChurn ?? [])

  console.log(`ROSTER_CHURN [${label}] total=${events.length}\n${events.slice(-120).join('\n')}`)
}

async function selectedMainChat(page: Page) {
  const tab = page.locator('[data-zone-tabstrip="grp-main"] [data-tree-tab][aria-selected="true"]')

  await expect(tab).toHaveCount(1, { timeout: 15_000 })
  const anchor = await tab.getAttribute('data-tree-tab')

  if (!anchor || !/^(workspace|session-tile:.+)$/.test(anchor)) {
    throw new Error(`Expected a selected main chat tab, got ${anchor ?? 'no pane id'}`)
  }

  const surface = page.locator(`[data-session-anchor="${anchor}"]`)

  await expect(surface).toBeVisible({ timeout: 15_000 })

  return { anchor, surface, tab }
}

async function activeMainChat(page: Page) {
  const tabStrip = page.locator('[data-zone-tabstrip="grp-main"]')

  if ((await tabStrip.count()) > 0) {
    return selectedMainChat(page)
  }

  const surface = page.locator('[data-session-anchor="workspace"]')

  await expect(surface).toBeVisible({ timeout: 15_000 })

  return { anchor: 'workspace', surface }
}

async function seedBot(hermesHome: string, mockUrl: string, name: string): Promise<void> {
  const dir = path.join(hermesHome, 'profiles', name)
  fs.mkdirSync(dir, { recursive: true })
  writeMockProviderConfig(dir, mockUrl, '  language: en')
  writeEnvFile(dir)

  const builder = await RealSessionBuilder.start(dir)

  try {
    await builder.createSession({ title: 'Bot Chat', turns: [`Hello ${name}`] })
  } finally {
    await builder.close()
  }
}

test.beforeAll(async () => {
  const mock = await startMockServer()
  const sandbox = createSandbox('bots-sync')
  writeMockProviderConfig(sandbox.hermesHome, mock.url, '  language: en')
  writeEnvFile(sandbox.hermesHome)
  await seedBot(sandbox.hermesHome, mock.url, 'alpha')
  await seedBot(sandbox.hermesHome, mock.url, 'beta')

  const { app, page } = await launchDesktop(buildAppEnv(sandbox))

  fixture = {
    app,
    page,
    mock,
    mockUrl: mock.url,
    sandbox,
    cleanup: async () => {
      await app.close().catch(() => undefined)
      await mock.close()
      sandbox.cleanup()
    }
  }
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test.afterEach(async ({}, testInfo) => {
  // Temporary while collecting samples: dump on every run, not only on failure.
  if (fixture) {
    await dumpRosterChurn(fixture.page, testInfo.title)
  }
})

test('a bot row click lands on the Bot Chat the row previews, not a side thread', async () => {
  test.setTimeout(300_000)
  const page = fixture!.page

  await openBots(page)
  await watchRosterChurn(page)

  const botsRoster = roster(page)
  const alphaRow = botRow(page, 'alpha')
  const betaRow = botRow(page, 'beta')
  await expect(alphaRow).toBeVisible({ timeout: 30_000 })
  await expect(betaRow).toBeVisible({ timeout: 30_000 })

  await alphaRow.click()
  await expect(botsRoster).toBeHidden({ timeout: 30_000 })
  const alphaChat = await activeMainChat(page)
  const alphaTranscript = alphaChat.surface.locator('[data-slot="aui_thread-viewport"]')
  await expect(alphaTranscript).toBeVisible({ timeout: 45_000 })
  await expect(alphaTranscript).toContainText('Hello alpha', { timeout: 45_000 })
  const alphaComposer = alphaChat.surface
    .locator('[data-slot="composer-root"] [contenteditable="true"]')
    .filter({ visible: true })
    .first()
  await expect(alphaComposer).toBeVisible({ timeout: 15_000 })
  await settle(page, 15_000)

  // A `+` side thread for alpha, with a real turn so it is a persisted tile.
  await page.keyboard.press('Control+t')
  const mainTabStrip = page.locator('[data-zone-tabstrip="grp-main"]')
  const draftChat = await selectedMainChat(page)
  expect(draftChat.anchor).toMatch(/^session-tile:/)
  const draftComposer = draftChat.surface
    .locator('[data-slot="composer-root"] [contenteditable="true"]')
    .filter({ visible: true })
    .first()
  await expect(draftComposer).toBeVisible({ timeout: 15_000 })
  await draftComposer.click()
  await draftComposer.fill('hello alpha thread')
  await page.keyboard.press('Enter')
  const sideTab = mainTabStrip.getByRole('tab', { name: 'hello alpha thread' }).first()
  await expect(sideTab).toBeVisible({ timeout: 60_000 })
  const sideTabId = await sideTab.getAttribute('data-tree-tab')
  expect(sideTabId).toMatch(/^session-tile:/)
  await sideTab.click()
  const sideChat = await selectedMainChat(page)
  expect(sideChat.anchor).toBe(sideTabId)
  const sideTranscript = sideChat.surface.locator('[data-slot="aui_thread-viewport"]')
  await expect(sideTranscript).toBeVisible({ timeout: 60_000 })
  await expect(sideTranscript).toContainText(MOCK_REPLY, { timeout: 60_000 })

  // Leave alpha on the side thread, go to beta, come back via the row.
  await openBots(page)
  await expect(betaRow).toBeVisible({ timeout: 30_000 })
  await betaRow.click()
  await expect(botsRoster).toBeHidden({ timeout: 30_000 })
  const betaChat = await activeMainChat(page)
  const betaTranscript = betaChat.surface.locator('[data-slot="aui_thread-viewport"]')
  await expect(betaTranscript).toBeVisible({ timeout: 60_000 })
  await expect(betaTranscript).toContainText('Hello beta', { timeout: 60_000 })
  await expect(
    betaChat.surface.locator('[data-slot="composer-root"] [contenteditable="true"]').filter({ visible: true }).first()
  ).toBeVisible({ timeout: 15_000 })
  await settle(page)

  await openBots(page)
  await expect(alphaRow).toBeVisible({ timeout: 30_000 })
  await alphaRow.click()
  await expect(botsRoster).toBeHidden({ timeout: 30_000 })
  // The scoped row click leaves the roster and selects the canonical transcript
  // with its own composer, rather than merely finding the row's sidebar preview.
  const returnedAlphaChat = await activeMainChat(page)
  const returnedAlphaTranscript = returnedAlphaChat.surface.locator('[data-slot="aui_thread-viewport"]')
  await expect(returnedAlphaTranscript).toBeVisible({ timeout: 45_000 })
  await expect(returnedAlphaTranscript).toContainText('Hello alpha', { timeout: 45_000 })
  await expect(
    returnedAlphaChat.surface
      .locator('[data-slot="composer-root"] [contenteditable="true"]')
      .filter({ visible: true })
      .first()
  ).toBeVisible({ timeout: 15_000 })
  // The side thread is still open beside it (scoped to alpha), not closed.
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            [...document.querySelectorAll<HTMLElement>('[data-zone-tabstrip="grp-main"] [data-tree-tab]')]
              .map(element => element.getAttribute('data-tree-tab') ?? '')
              .filter(id => id.startsWith('session-tile:')).length
        ),
      { timeout: 15_000 }
    )
    .toBe(1)
})
