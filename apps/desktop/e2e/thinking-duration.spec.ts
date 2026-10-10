/**
 * The reasoning row under an assistant reply. While the block streams it reads
 * as thinking; once the turn settles it keeps how long the model thought, and
 * that survives the transcript being re-read.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Auto-title makes its own model call; the sandbox config switches it off so the
// scripted replies stay the only traffic. Same as the other scripted specs.
const DISABLE_AUTO_TITLE = 'auxiliary:\n  title_generation:\n    enabled: false'

const TRIGGER = 'E2E_THINK'
// Under SIDEBAR_DOCK_MIN_WIDTH_PX (640) and above the main window's minimum (400).
const NARROW_WIDTH = 560
// Long enough that the live block shows its timer for a few seconds.
const REASONING = '先看看问题的背景，再逐条核对依赖关系，确认每一步都和原始需求一致，最后整理出结论。'.repeat(2)
const REPLY = 'E2E_THINK_REPLY 分析已经完成，结论见上。'
// zh "已思考 12 秒", en "Thought for 12s": a settled block that kept its span.
const SETTLED_WITH_DURATION = /(已思考|Thought for) \d/

// THINKING_TITLE_SCREENSHOT_DIR=<dir> saves captures of the key states for design
// review; never part of the assertions.
async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.THINKING_TITLE_SCREENSHOT_DIR

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
      reasoningForPrompt: prompt => (prompt.includes(TRIGGER) ? REASONING : undefined),
      replyForPrompt: prompt => (prompt.includes(TRIGGER) ? REPLY : 'Plain reply.'),
    },
  })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('a settled reasoning block keeps how long the model thought, even after the session is reopened', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  const composer = page.locator('[contenteditable="true"]').first()
  await composer.waitFor({ state: 'visible', timeout: 10_000 })
  await composer.click()
  await composer.type(`${TRIGGER} 帮我看看这个问题`, { delay: 20 })
  await page.keyboard.press('Enter')

  const thinking = page.locator('[data-slot="aui_thinking-disclosure"]').first()

  await expect(thinking).toContainText(/思考中|Thinking/, { timeout: 60_000 })
  await capture(page, '1-thinking-live')

  await expect(page.getByText(REPLY)).toBeVisible({ timeout: 90_000 })
  await expect(thinking).toContainText(SETTLED_WITH_DURATION, { timeout: 15_000 })
  await capture(page, '2-thinking-settled')

  // The settled turn is re-read from the stored transcript in some paths; the
  // duration must come through that re-read, not only the live render.
  await page.waitForTimeout(4_000)
  await expect(thinking).toContainText(SETTLED_WITH_DURATION)

  // Leave the session and come back: the row is rebuilt from the session state.
  await page.getByRole('button', { name: /新建对话|New chat/ }).first().click()
  await page.locator('[data-slot="sidebar"] button').filter({ hasText: TRIGGER }).first().click()

  await expect(page.getByText(REPLY)).toBeVisible({ timeout: 30_000 })
  await expect(thinking).toContainText(SETTLED_WITH_DURATION, { timeout: 15_000 })
  await capture(page, '3-thinking-reopened')

  // Narrow window: the settled row reads the same in the thread at 560px.
  await fixture!.app.evaluate(({ BrowserWindow }, width) => {
    BrowserWindow.getAllWindows()[0].setSize(width, 820)
  }, NARROW_WIDTH)
  // The breakpoint is a media query; give the layout a frame to re-evaluate.
  await page.waitForTimeout(600)
  await expect(thinking).toContainText(SETTLED_WITH_DURATION)
  await capture(page, '4-thinking-settled-narrow')
})
