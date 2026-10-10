/**
 * The sidebar filter menu in Chinese. Its option labels used to be English
 * literals on zh screens; they now come from the desktop i18n bundles. The
 * narrow case opens the same menu from the sessions overlay below the sidebar
 * breakpoint.
 *
 * COPY_LOCALIZATION_SCREENSHOT_DIR=<dir> saves captures of the states for
 * design review; the captures are never part of the assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Under SIDEBAR_DOCK_MIN_WIDTH_PX (640) and above the main window's minimum (400).
const NARROW_WIDTH = 560

// Labels the zh filter menu shows in place of the English ones. "项目" (Project)
// is not asserted: its submenu only renders once the profile has projects.
const ZH_LABELS = ['分组方式', '排序方式', '显示', '状态', '拉取请求', '配置档案', '收件箱样式', '已归档']

const ENGLISH_LABELS = [
  'Grouping',
  'Ordering',
  'Show',
  'Status',
  'Pull request',
  'Inbox style',
  'Archived',
  'Reset to defaults',
  'Expand all',
  'Collapse all'
]

type Page = MockBackendFixture['page']

// Rows are menuitems, or menuitemcheckboxes for the toggles.
function menuRows(page: Page) {
  return page.getByRole('menu').locator('[role="menuitem"], [role="menuitemcheckbox"]')
}

async function capture(page: Page, name: string): Promise<void> {
  const dir = process.env.COPY_LOCALIZATION_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: zh' })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('the sidebar filter menu reads in Chinese at the default width', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await page.getByRole('button', { name: '筛选' }).first().click()

  for (const label of ZH_LABELS) {
    await expect(menuRows(page).filter({ hasText: label }).first()).toBeVisible({ timeout: 10_000 })
  }

  for (const label of ENGLISH_LABELS) {
    await expect(menuRows(page).filter({ hasText: label })).toHaveCount(0)
  }

  await capture(page, '1-filter-menu-zh-1220x800')
  await page.keyboard.press('Escape')
})

test('the sidebar filter menu reads in Chinese at a narrow width', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await fixture!.app.evaluate(({ BrowserWindow }, width) => {
    BrowserWindow.getAllWindows()[0].setSize(width, 820)
  }, NARROW_WIDTH)
  // The breakpoint is a media query; give the layout a frame to re-evaluate.
  await page.waitForTimeout(600)

  // Ctrl+B is view.toggleSidebar: at this width it opens the sessions overlay.
  await page.keyboard.press('Control+b')
  await page.getByRole('button', { name: '筛选' }).first().click()

  for (const label of ZH_LABELS) {
    await expect(menuRows(page).filter({ hasText: label }).first()).toBeVisible({ timeout: 10_000 })
  }

  for (const label of ENGLISH_LABELS) {
    await expect(menuRows(page).filter({ hasText: label })).toHaveCount(0)
  }

  await capture(page, '2-filter-menu-zh-560x820')
  await page.keyboard.press('Escape')
})
