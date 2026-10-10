/**
 * The agent space is a page beside the nav, like Tools and Plugins: the product
 * nav stays on screen, the roster fills the workspace, opening a bot shows its
 * chat in the same place, and any nav item leaves the page.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

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

test('the agent space is a page beside the nav, and a nav item leaves it', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  const roster = page.locator('[data-slot="bots-roster"]')
  const navNewChat = page.getByRole('button', { name: /新建对话|New chat/ }).first()
  const navAgentSpace = page.getByRole('button', { name: /智能体空间|Agent space/ }).first()

  await navAgentSpace.click()
  await expect(roster).toBeVisible({ timeout: 20_000 })
  // The nav stays on screen while the roster page is open.
  await expect(navNewChat).toBeVisible()
  await capture(page, '1-agent-space-page')

  // Opening a bot shows its chat where the page was, and the page closes.
  await page.locator('[data-slot="bots-roster"] [data-roster-key]').first().click()
  await expect(roster).toBeHidden({ timeout: 30_000 })
  await expect(navNewChat).toBeVisible()
  await capture(page, '2-bot-chat-from-page')

  // Any nav item leaves the page for its own page, like Tools and Plugins.
  await navAgentSpace.click()
  await expect(roster).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: /^(任务|Tasks)$/ }).first().click()
  await expect(roster).toBeHidden({ timeout: 10_000 })
  await expect(navNewChat).toBeVisible()
})
