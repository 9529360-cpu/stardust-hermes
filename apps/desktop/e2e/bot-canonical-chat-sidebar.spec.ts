/**
 * A bot's canonical Bot Chat is hidden by design and is reached only through
 * its bot row. Opening it must not put it into the sidebar's conversation list,
 * even while it is the open, focused chat.
 */
import fs from 'node:fs'
import path from 'node:path'

import { MOCK_REPLY } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// BOT_SIDEBAR_SCREENSHOT_DIR=<dir> with BOT_SIDEBAR_SCREENSHOT_LABEL=<before|after>
// saves the window after the open, for review; never part of the assertions.
async function capture(page: MockBackendFixture['page'], label: string): Promise<void> {
  const dir = process.env.BOT_SIDEBAR_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${label}-bot-chat-open.png`) })
}

// The untitled fallback a row shows when it has no name and no first message.
const UNTITLED = /^(无标题会话|Untitled session)$/
// The conversation list when it holds no rows.
const EMPTY_LIST = /^(暂无会话|No sessions yet)$/

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('opening the default bot row never lists its canonical Bot Chat in the sidebar', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  const roster = page.locator('[data-slot="bots-roster"]')
  await page
    .getByRole('button', { name: /智能体空间|Agent space/ })
    .first()
    .click()
  await expect(roster).toBeVisible({ timeout: 20_000 })

  // The default profile is the first bot row on a fresh install.
  await page.locator('[data-slot="bots-roster"] [data-roster-key]').first().click()
  await expect(roster).toBeHidden({ timeout: 30_000 })

  // The Bot Chat is the open, focused chat: its empty state is on screen.
  await expect(page.locator('[data-slot="bot_chat_empty"]')).toBeVisible({ timeout: 60_000 })

  // A first turn proves the chat is live. Its open has already resolved the
  // chat into the sidebar cache, so an empty list after that is a real answer.
  const composer = page.locator('[data-slot="composer-root"] [contenteditable="true"]').filter({ visible: true }).first()
  await expect(composer).toBeVisible({ timeout: 15_000 })
  await composer.click()
  await composer.fill('hello bot')
  await page.keyboard.press('Enter')
  await expect(page.getByText(MOCK_REPLY).filter({ visible: true }).first()).toBeVisible({ timeout: 60_000 })

  const sidebar = page.locator('[data-tour="sessions-sidebar"]')

  try {
    await expect(sidebar.getByText(EMPTY_LIST)).toBeVisible({ timeout: 15_000 })
    await expect(sidebar.getByText(UNTITLED)).toHaveCount(0)
    await expect(sidebar.getByText('Bot Chat', { exact: true })).toHaveCount(0)
  } finally {
    await capture(page, process.env.BOT_SIDEBAR_SCREENSHOT_LABEL ?? 'run')
  }
})
