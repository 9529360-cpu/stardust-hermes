/**
 * Narrow windows: below the sidebar breakpoint the sessions zone is an edge
 * overlay with its own reveal state. An explicit reveal (the agent-space row,
 * the roster's back control) has to move that overlay too, or the roster and
 * the way back to the product nav get stuck behind whichever pane it showed.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Under SIDEBAR_DOCK_MIN_WIDTH_PX (640) and above the main window's minimum (400).
const NARROW_WIDTH = 560

// ROSTER_NARROW_SCREENSHOT_DIR=<dir> saves captures of the key states for design
// review; never part of the assertions.
async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.ROSTER_NARROW_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

let fixture: MockBackendFixture | null = null

test.beforeEach(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
  await fixture.app.evaluate(({ BrowserWindow }, width) => {
    BrowserWindow.getAllWindows()[0].setSize(width, 820)
  }, NARROW_WIDTH)
  // The breakpoint is a media query; give the layout a frame to re-evaluate.
  await fixture.page.waitForTimeout(600)
})

test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = null
})

const roster = (page: MockBackendFixture['page']) => page.locator('[data-slot="bots-roster"]')

const navNewChat = (page: MockBackendFixture['page']) =>
  page.getByRole('button', { name: /新建对话|New chat/ }).first()

const backControl = (page: MockBackendFixture['page']) =>
  page.getByRole('button', { name: /返回对话|Back to conversations/ })

test('agent space opens the roster in the overlay, and its back control returns to the nav', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page

  // Ctrl+B is view.toggleSidebar: at this width it opens the sessions overlay.
  await page.keyboard.press('Control+b')
  await page.getByRole('button', { name: /智能体空间|Agent space/ }).first().click()

  await expect(roster(page)).toBeVisible({ timeout: 20_000 })
  await capture(page, '1-narrow-agent-space-roster')

  await backControl(page).click()
  await expect(navNewChat(page)).toBeVisible({ timeout: 10_000 })
  await expect(roster(page)).toBeHidden()
  await capture(page, '2-narrow-back-to-nav')
})

test('the back control closes a roster overlay that was opened from its own tab', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page

  await page.keyboard.press('Control+b')
  await page.locator('[data-narrow-overlay-tab="hermes-bots:pane"]').click()
  await expect(roster(page)).toBeVisible({ timeout: 20_000 })

  await backControl(page).click()
  await expect(navNewChat(page)).toBeVisible({ timeout: 10_000 })
  await expect(roster(page)).toBeHidden()
})
