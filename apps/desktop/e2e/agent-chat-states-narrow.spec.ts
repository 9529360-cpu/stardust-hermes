/**
 * The bot chat's empty state and the Routines rail icon below the sidebar
 * breakpoint. There the product nav lives in the sessions overlay, so the bot is
 * opened from that overlay, as the agent space page is.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Under SIDEBAR_DOCK_MIN_WIDTH_PX (640) and above the main window's minimum (400).
const NARROW_WIDTH = 560

// AGENT_CHAT_STATES_SCREENSHOT_DIR=<dir> saves captures of the key states for
// design review; never part of the assertions.
async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.AGENT_CHAT_STATES_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('at a narrow width an empty bot chat greets and seats the Routines rail icon', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await fixture!.app.evaluate(({ BrowserWindow }, width) => {
    BrowserWindow.getAllWindows()[0].setSize(width, 820)
  }, NARROW_WIDTH)
  // The breakpoint is a media query; give the layout a frame to re-evaluate.
  await page.waitForTimeout(600)

  const roster = page.locator('[data-slot="bots-roster"]')
  const empty = page.locator('[data-slot="bot_chat_empty"]')
  const starters = page.locator('[data-slot="bot_chat_starters"] button')
  const routinesRail = page.locator('[data-tree-tab$=":routines"][data-vertical]')

  // Ctrl+B is view.toggleSidebar: at this width it opens the sessions overlay.
  await page.keyboard.press('Control+b')
  await page.getByRole('button', { name: /智能体空间|Agent space/ }).first().click()
  await expect(roster).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('[data-tree-tab$=":routines"]')).toHaveCount(0)
  await capture(page, 'narrow-1-roster-default-bot')

  await page.locator('[data-slot="bots-roster"] [data-roster-key]').first().click()
  await expect(roster).toBeHidden({ timeout: 30_000 })
  await expect(empty).toBeVisible({ timeout: 20_000 })
  await expect(starters).toHaveCount(3)
  await expect(routinesRail).toHaveCount(1)
  await capture(page, 'narrow-2-bot-chat-empty')
})
