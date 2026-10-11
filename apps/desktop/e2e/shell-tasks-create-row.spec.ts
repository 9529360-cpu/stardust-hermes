/**
 * The Tasks list has one create control: a labelled row at the top of the list, aligned
 * with the search field and the rows, the same at the full and the narrow width.
 *
 * SHELL_STATES_SCREENSHOT_DIR=<dir> saves captures for design review; never part of the
 * assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const CREATE_LABEL = /新建定时任务|New scheduled task|New cron job/
const NARROW_WIDTH = 560
const NARROW_HEIGHT = 820

let fixture: MockBackendFixture | null = null

async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.SHELL_STATES_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

/** Opens a new chat, which has the composer that waitForAppReady looks for. */
async function toComposer(page: MockBackendFixture['page']): Promise<void> {
  await page.getByRole('button', { name: /^(新建对话|New chat)$/ }).first().click()
  await page.waitForSelector('textarea, [contenteditable="true"]', { state: 'attached', timeout: 30_000 })
}

async function openTasks(page: MockBackendFixture['page'], narrow: boolean): Promise<void> {
  const tasks = page.getByRole('button', { name: /^(任务|Tasks)$/ }).first()

  if (narrow && !(await tasks.isVisible().catch(() => false))) {
    await page.keyboard.press('Control+b')
    await expect(tasks).toBeVisible({ timeout: 10_000 })
  }

  await tasks.click()
  await expect(page.locator('[data-cron-page]')).toBeVisible({ timeout: 20_000 })
}

async function expectCreateRowAligned(page: MockBackendFixture['page'], widthName: string): Promise<void> {
  const create = page.getByRole('button', { name: CREATE_LABEL })
  const search = page.locator('[data-cron-page] input').first()

  // One control, with its label visible, not an icon floating in the list. The list is read
  // from the backend after the page opens, so the control appears a moment later.
  await expect(create).toHaveCount(1, { timeout: 20_000 })
  await expect(create).toBeVisible({ timeout: 20_000 })
  await expect(create).toHaveText(CREATE_LABEL)

  // The search field's wrapper is the list's left edge; its input sits inside it, after the icon.
  const searchRow = search.locator('xpath=..')
  const createBox = await create.boundingBox()
  const searchBox = await search.boundingBox()
  const searchRowBox = await searchRow.boundingBox()
  expect(createBox).not.toBeNull()
  expect(searchBox).not.toBeNull()
  expect(searchRowBox).not.toBeNull()

  // Aligned with the list's left edge, and the same row height at both widths.
  expect(Math.abs((createBox?.x ?? 0) - (searchRowBox?.x ?? 0))).toBeLessThan(2)
  expect(createBox?.height ?? 0).toBeGreaterThan(20)
  expect(createBox?.height ?? 0).toBeLessThan(40)

  // The control sits under the search field, at the top of the list.
  expect(createBox?.y ?? 0).toBeGreaterThan(searchBox?.y ?? 0)

  await capture(page, `${widthName}-tasks-create-row`)
}

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('at 1220x800 the create control is one labelled row at the top of the Tasks list', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await openTasks(page, false)
  await expectCreateRowAligned(page, '1220x800')
})

test('at 560x820 the create control is the same labelled row in the Tasks list', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  // The previous test leaves the Tasks page open, which has no composer to wait for.
  await toComposer(page)
  await waitForAppReady(fixture!, 120_000)

  await fixture!.app.evaluate(
    ({ BrowserWindow }, size) => {
      BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
    },
    { width: NARROW_WIDTH, height: NARROW_HEIGHT }
  )
  await page.waitForTimeout(600)

  await openTasks(page, true)
  await expectCreateRowAligned(page, '560x820')
})
