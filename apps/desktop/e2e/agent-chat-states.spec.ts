/**
 * A bot's chat before it has spoken, and the Routines tile beside it, in the
 * real window. The roster page has no Routines tile. Opening a bot shows an
 * empty chat that names the bot and offers starters, and seats the tile as a
 * rail icon. A starter only fills the composer. A new chat takes the tile away.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

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

test('an empty bot chat greets, offers starters, and seats the Routines tile as an icon', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  const roster = page.locator('[data-slot="bots-roster"]')
  const empty = page.locator('[data-slot="bot_chat_empty"]')
  const starters = page.locator('[data-slot="bot_chat_starters"] button')
  // The Routines tile is the pane whose id ends in ":routines"; collapsed, it is
  // the right edge's rail.
  const routinesTab = page.locator('[data-tree-tab$=":routines"]')
  const routinesRail = page.locator('[data-tree-tab$=":routines"][data-vertical]')

  // The roster is not a bot chat, so the Routines tile is not seated beside it.
  await page.getByRole('button', { name: /智能体空间|Agent space/ }).first().click()
  await expect(roster).toBeVisible({ timeout: 20_000 })
  await expect(routinesTab).toHaveCount(0)
  await capture(page, '1-roster-default-bot')

  // Opening the bot shows its empty chat and seats the tile as an icon.
  await page.locator('[data-slot="bots-roster"] [data-roster-key]').first().click()
  await expect(roster).toBeHidden({ timeout: 30_000 })
  await expect(empty).toBeVisible({ timeout: 20_000 })
  await expect(starters).toHaveCount(3)
  await expect(routinesRail).toHaveCount(1)
  await capture(page, '2-bot-chat-empty')

  // The icon names the tile on hover.
  await routinesRail.hover()
  await expect(page.getByRole('tooltip').filter({ hasText: /定时任务|Scheduled jobs/ })).toBeVisible({
    timeout: 10_000
  })
  await page.mouse.move(0, 0)

  // A starter fills the composer and is not sent: the chat stays empty.
  const prompt = ((await starters.first().textContent()) ?? '').trim()
  await starters.first().click()
  await expect(page.locator('[contenteditable="true"]').first()).toHaveText(prompt)
  await expect(empty).toBeVisible()
  await capture(page, '3-starter-filled')

  // Expanding the tile puts its name back in the tab as text.
  await routinesRail.click()
  await expect(page.locator('[data-tree-tab$=":routines"]:not([data-vertical])')).toHaveCount(1, { timeout: 10_000 })
  await capture(page, '4-routines-expanded')

  // A new chat is not a bot chat, so the tile goes away again.
  await page.getByRole('button', { name: /新建对话|New chat/ }).first().click()
  await expect(routinesTab).toHaveCount(0)
  await capture(page, '5-new-chat-no-routines')
})
