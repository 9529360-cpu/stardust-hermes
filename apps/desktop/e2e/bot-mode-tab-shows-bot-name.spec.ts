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

// Every bot's canonical chat is STORED under the same title ("Bot Chat" — the
// name the gateway resolves it by), so the main tab strip captioned every open
// bot chat identically and two bots' tabs were indistinguishable (#99152). The
// tab must read the bot's display name while the stored title stays canonical.

type Page = MockBackendFixture['page']

let fixture: MockBackendFixture | null = null

async function openBots(page: Page): Promise<void> {
  const tab = page.getByRole('button', { name: /智能体空间|Agent space/ }).first()

  await tab.click()
  await expect(page.getByRole('button', { name: 'New bot or group chat' })).toBeVisible()
}

const roster = (page: Page) => page.locator('[data-slot="bots-roster"]')
const botRow = (page: Page, name: string) => roster(page).locator(`[data-roster-key="local::${name}"]`)

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
  const sandbox = createSandbox('bots-tabname')
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

test("an open Bot Chat's tab reads the bot's name, not the canonical 'Bot Chat' title", async () => {
  test.setTimeout(300_000)
  const page = fixture!.page

  await openBots(page)

  const botsRoster = roster(page)
  const alphaRow = botRow(page, 'alpha')
  await expect(alphaRow).toBeVisible({ timeout: 30_000 })

  await alphaRow.click()
  await expect(botsRoster).toBeHidden({ timeout: 30_000 })
  const canonicalChat = await activeMainChat(page)
  const canonicalPaneId = canonicalChat.anchor
  await expect(canonicalChat.surface.getByText('Hello alpha', { exact: true })).toBeVisible({ timeout: 45_000 })
  await expect(
    canonicalChat.surface
      .locator('[data-slot="composer-root"] [contenteditable="true"]')
      .filter({ visible: true })
      .first()
  ).toBeVisible({ timeout: 15_000 })

  // A `+` side thread beside the Bot Chat gives the main zone a tab strip —
  // the surface where every bot chat used to read "Bot Chat".
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
  await expect(sideChat.surface.getByText(MOCK_REPLY).first()).toBeVisible({ timeout: 60_000 })

  const canonicalTab = mainTabStrip.locator(`[data-tree-tab="${canonicalPaneId}"]`)
  await canonicalTab.click()
  const activeCanonicalChat = await selectedMainChat(page)
  await expect(activeCanonicalChat.tab).toHaveAttribute('data-tree-tab', canonicalPaneId)
  await expect(activeCanonicalChat.tab).toContainText(/alpha/i)
  await expect(activeCanonicalChat.tab).not.toContainText(/bot chat/i)
  await expect(activeCanonicalChat.surface.getByText('Hello alpha', { exact: true })).toBeVisible({ timeout: 15_000 })
  await expect(
    activeCanonicalChat.surface
      .locator('[data-slot="composer-root"] [contenteditable="true"]')
      .filter({ visible: true })
      .first()
  ).toBeVisible({ timeout: 15_000 })
})
