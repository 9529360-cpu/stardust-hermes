/**
 * A settled reasoning block keeps the duration the live view showed, across a window reload. The span is
 * the backend's own clock (stamped on the stream, stored on the assistant row), so the reloaded label is
 * the label the user saw, not a number the renderer re-derives.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Auto-title makes its own model call; the sandbox config switches it off so the scripted replies stay the
// only traffic. Same as the other scripted specs.
const DISABLE_AUTO_TITLE = 'auxiliary:\n  title_generation:\n    enabled: false'

const TRIGGER = 'E2E_RELOAD_THINK'
const WINDOW = { height: 800, width: 1220 }
// Long enough that the scripted reasoning streams for a few seconds before the reply starts.
const REASONING = '先看看问题的背景，再逐条核对依赖关系，确认每一步都和原始需求一致，最后整理出结论。'.repeat(2)
const REPLY = 'E2E_RELOAD_REPLY 分析已经完成，结论见上。'
// zh "已思考 3 秒", en "Thought for 3s": a settled block that kept its span.
const SETTLED_WITH_DURATION = /(已思考|Thought for) \d/

// REASONING_DURATION_SCREENSHOT_DIR=<dir> saves captures of the before and after states for review; never
// part of the assertions.
async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.REASONING_DURATION_SCREENSHOT_DIR

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

test('a settled reasoning block shows the same duration after the window reloads', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page

  await fixture!.app.evaluate(({ BrowserWindow }, size) => {
    BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
  }, WINDOW)
  await waitForAppReady(fixture!, 120_000)

  const composer = page.locator('[contenteditable="true"]').first()
  await composer.waitFor({ state: 'visible', timeout: 10_000 })
  await composer.click()
  await composer.type(`${TRIGGER} 帮我看看这个问题`, { delay: 20 })
  await page.keyboard.press('Enter')

  const thinking = page.locator('[data-slot="aui_thinking-disclosure"]').first()

  await expect(page.getByText(REPLY)).toBeVisible({ timeout: 90_000 })
  await expect(thinking).toContainText(SETTLED_WITH_DURATION, { timeout: 15_000 })
  const beforeReload = ((await thinking.textContent()) ?? '').trim()
  await capture(page, '1-settled-before-reload')

  await page.reload()
  await waitForAppReady(fixture!, 120_000)

  await expect(page.getByText(REPLY)).toBeVisible({ timeout: 60_000 })
  await expect(thinking).toContainText(SETTLED_WITH_DURATION, { timeout: 15_000 })
  await expect(thinking).toHaveText(beforeReload)
  await capture(page, '2-settled-after-reload')
})
