/**
 * Page titles and nouns, checked in the real window at full width and at a narrow width.
 *
 * - Tools and Plugins carry the same title row as Tasks, in the same place.
 * - The Projects list says there are no projects yet when there are none.
 * - The Command Center names conversations 对话, not 会话.
 *
 * PAGE_TITLES_SCREENSHOT_DIR=<dir> saves captures for design review; never part of the assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Under SIDEBAR_DOCK_MIN_WIDTH_PX (640): the product nav lives in the sessions overlay there.
const NARROW_WIDTH = 560
const NARROW_HEIGHT = 820
const WIDE_WIDTH = 1220
const WIDE_HEIGHT = 800

let fixture: MockBackendFixture | null = null

async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.PAGE_TITLES_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

async function captureLocator(
  locator: { screenshot: (options: { path: string }) => Promise<Buffer> },
  name: string
): Promise<void> {
  const dir = process.env.PAGE_TITLES_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await locator.screenshot({ path: path.join(dir, `${name}.png`) })
}

async function resizeWindow(width: number, height: number): Promise<void> {
  await fixture!.app.evaluate(
    ({ BrowserWindow }, size) => {
      BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
    },
    { width, height }
  )
  // The breakpoint is a media query; give the layout a frame to re-evaluate.
  await fixture!.page.waitForTimeout(600)
}

// One app per test: the second test must not inherit the first test's page (the Command Center).
test.beforeEach(async () => {
  fixture = await setupMockBackend()
})

test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = null
})

// The Command Center opens on its sessions section. Wait for that section's own copy, not the nav's.
async function openCommandCenter(page: MockBackendFixture['page']): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = '/command-center'
  })
  await expect(page.getByRole('button', { name: '关闭命令中心', exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('搜索与管理对话', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('搜索与管理会话', { exact: true })).toHaveCount(0)
}

test('Tools and Plugins share the Tasks title row, and the Projects list names the empty state', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)
  await resizeWindow(WIDE_WIDTH, WIDE_HEIGHT)

  const nav = page.getByRole('navigation', { name: 'Product navigation' })

  // Tasks is the reference: its title row sits at the top of the page body.
  await nav.getByRole('button', { name: '任务', exact: true }).click()
  const tasksTitle = page.getByRole('heading', { name: '定时任务', exact: true })
  await expect(tasksTitle).toBeVisible({ timeout: 20_000 })
  const tasksBox = await tasksTitle.boundingBox()
  expect(tasksBox).not.toBeNull()

  await nav.getByRole('button', { name: '工具', exact: true }).click()
  const toolsTitle = page.getByRole('heading', { name: '工具', exact: true })
  await expect(toolsTitle).toBeVisible({ timeout: 20_000 })
  const toolsBox = await toolsTitle.boundingBox()
  expect(toolsBox).not.toBeNull()
  expect(Math.abs(toolsBox!.y - tasksBox!.y)).toBeLessThanOrEqual(2)
  expect(Math.abs(toolsBox!.x - tasksBox!.x)).toBeLessThanOrEqual(2)
  await capture(page, `tools-${WIDE_WIDTH}`)

  await nav.getByRole('button', { name: '插件', exact: true }).click()
  await expect(page.getByRole('heading', { name: '插件', exact: true })).toBeVisible({ timeout: 20_000 })
  await capture(page, `plugins-${WIDE_WIDTH}`)

  await nav.getByRole('button', { name: '项目', exact: true }).click()
  await expect(nav.getByText('暂无项目', { exact: true })).toBeVisible({ timeout: 20_000 })
  await capture(page, `projects-${WIDE_WIDTH}`)
  await captureLocator(nav, `projects-nav-${WIDE_WIDTH}`)

  await openCommandCenter(page)
  await capture(page, `command-center-${WIDE_WIDTH}`)
})

test('at a narrow width the same pages open from the overlay, and the Projects empty state shows there', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)
  await resizeWindow(NARROW_WIDTH, NARROW_HEIGHT)

  // Ctrl+B is view.toggleSidebar: at this width it opens the sessions overlay that holds the nav.
  await page.keyboard.press('Control+b')
  const nav = page.getByRole('navigation', { name: 'Product navigation' })
  await expect(nav).toBeVisible({ timeout: 10_000 })

  await nav.getByRole('button', { name: '工具', exact: true }).click()
  await expect(page.getByRole('heading', { name: '工具', exact: true })).toBeVisible({ timeout: 20_000 })
  await capture(page, `tools-${NARROW_WIDTH}`)

  // Picking Tools closed the overlay, so one toggle reopens it for the next pick.
  await page.keyboard.press('Control+b')
  await nav.getByRole('button', { name: '插件', exact: true }).click()
  await expect(page.getByRole('heading', { name: '插件', exact: true })).toBeVisible({ timeout: 20_000 })
  await capture(page, `plugins-${NARROW_WIDTH}`)

  // Picking Plugins closed the overlay too; reopen it for the Projects row, which keeps it open.
  await page.keyboard.press('Control+b')
  await nav.getByRole('button', { name: '项目', exact: true }).click()
  await expect(nav.getByText('暂无项目', { exact: true })).toBeVisible({ timeout: 20_000 })
  await capture(page, `projects-${NARROW_WIDTH}`)
  await captureLocator(nav, `projects-nav-${NARROW_WIDTH}`)

  await page.keyboard.press('Control+b')
  await openCommandCenter(page)
  await capture(page, `command-center-${NARROW_WIDTH}`)
})
