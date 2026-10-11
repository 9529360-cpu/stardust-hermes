/**
 * The composer's model pill keeps the model's name in a narrow row, truncated, instead of
 * collapsing to a bare chevron. The full-width pill is unchanged.
 *
 * SHELL_STATES_SCREENSHOT_DIR=<dir> saves captures for design review; never part of the
 * assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// The pill's accessible name carries the mock sandbox's model id.
const MODEL_PILL = /mock-model/
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

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('at 1220x800 the composer shows the model name in its pill', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  const pill = page.getByRole('button', { name: MODEL_PILL }).first()
  await expect(pill).toBeVisible()
  await expect(pill).toContainText(/Mock Model/i)
  await capture(page, '1220x800-composer')
})

test('at 560x820 the composer keeps the model name visible, in a shorter pill', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await fixture!.app.evaluate(
    ({ BrowserWindow }, size) => {
      BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
    },
    { width: NARROW_WIDTH, height: NARROW_HEIGHT }
  )
  await page.waitForTimeout(600)

  const pill = page.getByRole('button', { name: MODEL_PILL }).first()
  await expect(pill).toBeVisible()
  await expect(pill).toContainText(/Mock/i)

  const box = await pill.boundingBox()
  expect(box).not.toBeNull()
  // Shorter than the full pill (max-w-40) and wider than an icon: the name is readable.
  expect(box?.width ?? 0).toBeLessThanOrEqual(100)
  expect(box?.width ?? 0).toBeGreaterThan(40)

  await capture(page, '560x820-composer')
})
