/**
 * A routine created in chat shows its result card in Chinese. The mock provider
 * scripts a real `cronjob_manage` create, so the card renders what the backend
 * returned for the saved routine, not a fixture.
 *
 * CRON_RESULT_LABELS_SCREENSHOT_DIR=<dir> saves a capture of the card for design
 * review; the capture is never part of the assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { CRON_ROUTINE_REPLY, CRON_ROUTINE_TRIGGER } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const WINDOW = { height: 800, width: 1220 }

type Page = MockBackendFixture['page']

async function capture(page: Page, name: string): Promise<void> {
  const dir = process.env.CRON_RESULT_LABELS_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  // Tool Search defers cronjob_manage behind tool_call by default. The routine must be a direct
  // call for the cron card to render it, so keep nothing deferred for this session.
  fixture = await setupMockBackend({
    extraConfig: 'tools:\n  tool_search:\n    defer: []',
    extraDisplayConfig: '  language: zh'
  })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('a saved routine card reads in Chinese at 1220x800', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await fixture!.app.evaluate(({ BrowserWindow }, size) => {
    BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
  }, WINDOW)
  // The layout re-evaluates its breakpoints on resize; give it a frame.
  await page.waitForTimeout(600)

  const composer = page.locator('[contenteditable="true"]').first()
  await composer.waitFor({ state: 'visible', timeout: 10_000 })
  await composer.click()
  await composer.type(CRON_ROUTINE_TRIGGER, { delay: 20 })
  await page.keyboard.press('Enter')

  await expect(page.getByText(CRON_ROUTINE_REPLY).first()).toBeVisible({ timeout: 90_000 })

  // The routine is one tool call, so its row sits in a collapsed run summary until it opens.
  await page.locator('[data-tool-group]').first().locator('[aria-expanded]').first().click()

  // The collapsed row shows its title, which is what differs between the English and the Chinese card.
  const card = page
    .locator('[data-tool-row]')
    .filter({ hasText: /Cron 任务|定时任务/ })
    .first()
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.locator('[aria-expanded]').first().click()
  await page.waitForTimeout(300)
  await capture(page, 'routine-card')

  const text = (await card.innerText()).trim()

  expect(text).toContain('定时任务')
  expect(text).toContain('排程: 0 9 * * *')
  expect(text).toContain('重复: 永久')
  expect(text).toContain('投递: 当前对话')
  expect(text).not.toMatch(/[A-Za-z]/)
})
