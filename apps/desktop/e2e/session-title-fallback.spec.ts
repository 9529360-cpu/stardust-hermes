/**
 * A session with no title is named by its first message: one line, about forty
 * characters, in both the sidebar row and the conversation header. A real title
 * still wins, and the untitled label is never English on a Chinese screen.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Auto-title would name the session itself. Switching it off leaves the
// fallback as the only name the session can have.
const DISABLE_AUTO_TITLE = 'auxiliary:\n  title_generation:\n    enabled: false'

const TRIGGER = 'E2E_TITLE'
const PROMPT = `${TRIGGER} 请帮我把这周的进展、风险和下周计划整理成一份可以直接发给团队的周报，并列出每个负责人的待办`
const EXCERPT_START = '请帮我把这周的进展'
const REPLY = 'E2E_TITLE_REPLY 周报草稿已经整理好。'

// TITLE_FALLBACK_SCREENSHOT_DIR=<dir> saves captures of the key states for design
// review; never part of the assertions.
async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.TITLE_FALLBACK_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    extraConfig: DISABLE_AUTO_TITLE,
    mockServer: {
      replyForPrompt: prompt => (prompt.includes(TRIGGER) ? REPLY : 'Plain reply.'),
    },
  })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('an untitled session is named by the start of its first message, in the row and the header', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  const composer = page.locator('[contenteditable="true"]').first()
  await composer.waitFor({ state: 'visible', timeout: 10_000 })
  await composer.click()
  await composer.type(PROMPT, { delay: 5 })
  await page.keyboard.press('Enter')

  await expect(page.getByText(REPLY)).toBeVisible({ timeout: 90_000 })

  const row = page.locator('[data-slot="sidebar"] button').filter({ hasText: EXCERPT_START }).first()

  await expect(row).toBeVisible({ timeout: 30_000 })
  await expect(row).toContainText('…')
  // The tail of the message is cut away, and the English fallback never appears.
  await expect(row).not.toContainText('待办')
  await expect(row).not.toContainText('Untitled')

  await expect(page.locator('header').filter({ hasText: EXCERPT_START }).first()).toBeVisible()
  await capture(page, '1-untitled-session-named-by-first-message')
})
