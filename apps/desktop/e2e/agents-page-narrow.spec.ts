/**
 * The agent space page below the sidebar breakpoint. There the product nav
 * lives in the sessions overlay, so the page has to open from that overlay and
 * show in the main area, the same as at full width.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Under SIDEBAR_DOCK_MIN_WIDTH_PX (640) and above the main window's minimum (400).
const NARROW_WIDTH = 560

// AGENTS_PAGE_SCREENSHOT_DIR=<dir> saves captures of the key states for design
// review; never part of the assertions.
async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.AGENTS_PAGE_SCREENSHOT_DIR

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

test('at a narrow width the agent space page opens from the nav and a bot opens its chat', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await fixture!.app.evaluate(({ BrowserWindow }, width) => {
    BrowserWindow.getAllWindows()[0].setSize(width, 820)
  }, NARROW_WIDTH)
  // The breakpoint is a media query; give the layout a frame to re-evaluate.
  await page.waitForTimeout(600)

  const roster = page.locator('[data-slot="bots-roster"]')
  const navNewChat = page.getByRole('button', { name: /新建对话|New chat/ }).first()

  // Ctrl+B is view.toggleSidebar: at this width it opens the sessions overlay.
  await page.keyboard.press('Control+b')
  await page.getByRole('button', { name: /智能体空间|Agent space/ }).first().click()
  await expect(roster).toBeVisible({ timeout: 20_000 })
  // Picking the page closes the overlay, so the roster is not left under the nav.
  await expect(navNewChat).toBeHidden({ timeout: 10_000 })
  await capture(page, '1-narrow-agent-space-page')

  await page.locator('[data-slot="bots-roster"] [data-roster-key]').first().click()
  await expect(roster).toBeHidden({ timeout: 30_000 })
  await capture(page, '2-narrow-bot-chat')
})
