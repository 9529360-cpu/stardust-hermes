/**
 * Three states on a configured install, checked in the real window:
 *   1. The Tools page lists only toolsets that have tools. Speech-to-text has
 *      none (its switch lives in Settings → Voice), so it is not a toggle row.
 *   2. The composer effort pill reads the Settings word for the level (中).
 *      The compact composer at a narrow width folds the pill away by design,
 *      so the narrow pass captures the composer without asserting the pill.
 *   3. The local-model setup card does not offer to make a local model the
 *      default when a default model is already configured.
 * Each width launches its own app, so a resize never leaks into the other pass.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const NARROW_WIDTH = 560

// TOOLS_SETTINGS_SCREENSHOT_DIR=<dir> saves captures of the changed states for
// review; never part of the assertions.
async function capture(page: MockBackendFixture['page'], size: string, name: string): Promise<void> {
  const dir = process.env.TOOLS_SETTINGS_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${size}-${name}.png`) })
}

async function checkChangedStates(
  fixture: MockBackendFixture,
  size: string,
  { effortPill }: { effortPill: boolean }
): Promise<void> {
  const page = fixture.page

  if (effortPill) {
    await expect.soft(page.getByTestId('reasoning-pill')).toContainText('中', { timeout: 30_000 })
  }

  await capture(page, size, 'composer')

  // Tools page, toolsets tab.
  await page.evaluate(() => {
    window.location.hash = '#/skills?tab=toolsets'
  })
  await expect(page.getByText('文件操作', { exact: true }).first()).toBeVisible({ timeout: 30_000 })
  await expect.soft(page.getByText('语音转文字', { exact: true })).toHaveCount(0)
  await expect.soft(page.getByRole('switch', { name: /语音转文字/ })).toHaveCount(0)
  await capture(page, size, 'tools-toolsets')

  // Settings → Models: the local-model setup card, under a configured default.
  // A wide window shows the section list beside the page; a narrow one opens
  // the settings sheet with its section picker, so the list may not exist.
  await page.getByRole('button', { name: '打开设置' }).click()
  await expect(page.getByRole('button', { name: '关闭设置' })).toBeVisible({ timeout: 20_000 })
  const modelsSection = page.getByRole('complementary').getByRole('button', { name: '模型服务' })

  if ((await modelsSection.count()) > 0) {
    await modelsSection.click({ timeout: 30_000 })
  }

  await expect(page.getByText('自定义服务', { exact: true }).first()).toBeVisible({ timeout: 30_000 })

  const setupCard = page.getByRole('button', { name: '为我设置' })
  const runtimeHeading = page.getByText('本地运行时', { exact: true })
  await expect(setupCard.or(runtimeHeading).first()).toBeVisible({ timeout: 30_000 })
  await expect.soft(setupCard).toHaveCount(0)
  await expect.soft(runtimeHeading).toBeVisible()
  await capture(page, size, 'settings-models')
}

test('the changed states hold at the default window size', async () => {
  test.setTimeout(300_000)
  const fixture = await setupMockBackend()

  try {
    await waitForAppReady(fixture, 120_000)
    await checkChangedStates(fixture, '1220x800', { effortPill: true })
  } finally {
    await fixture.cleanup()
  }
})

test('the changed states hold at a narrow width', async () => {
  test.setTimeout(300_000)
  const fixture = await setupMockBackend()

  try {
    await waitForAppReady(fixture, 120_000)
    await fixture.app.evaluate(({ BrowserWindow }, width) => {
      BrowserWindow.getAllWindows()[0].setSize(width, 820)
    }, NARROW_WIDTH)
    // The breakpoint is a media query; give the layout a frame to re-evaluate.
    await fixture.page.waitForTimeout(600)
    await checkChangedStates(fixture, '560x820', { effortPill: false })
  } finally {
    await fixture.cleanup()
  }
})
