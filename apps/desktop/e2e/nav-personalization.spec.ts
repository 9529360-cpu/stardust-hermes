/**
 * The personal product nav: the 项目 section never lists the Home bucket, the 个性化 row opens
 * its panel at both widths, the panel's own link opens Settings on the appearance tab, and the
 * open page's nav row carries aria-current="page".
 *
 * NAV_PERSONALIZATION_SCREENSHOT_DIR=<dir> saves captures for design review; never part of the
 * assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { MOCK_REPLY } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, type Sandbox, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, type Page, test } from './test'

interface WindowSize {
  height: number
  name: string
  width: number
}

// 1220x800 is the fixture's own window. 560x820 sits below the 640px sidebar breakpoint, where
// the product nav lives in the sessions overlay.
const WIDE: WindowSize = { height: 800, name: '1220x800', width: 1220 }
const NARROW: WindowSize = { height: 820, name: '560x820', width: 560 }

const FIRST_CHAT = 'E2E 普通对话一'
const SECOND_CHAT = 'E2E 普通对话二'

let fixture: MockBackendFixture | null = null

// A chat that no folder claims is filed under the Home bucket. The backend does that when a
// session's cwd is the Stardust home itself, so pinning terminal.cwd there makes every chat
// created from the composer a plain chat. A repo or temp folder would become a project instead.
function pinPlainChatCwd(sandbox: Sandbox): void {
  fs.appendFileSync(
    path.join(sandbox.hermesHome, 'config.yaml'),
    `\nterminal:\n  cwd: ${JSON.stringify(sandbox.hermesHome)}\n`,
    'utf8'
  )
}

function productNav(page: Page) {
  return page.locator('[data-personal-product-nav]')
}

async function capture(page: Page, name: string): Promise<void> {
  const dir = process.env.NAV_PERSONALIZATION_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

async function setWindowSize(size: WindowSize): Promise<void> {
  await fixture!.app.evaluate(({ BrowserWindow }, target) => {
    BrowserWindow.getAllWindows()[0].setSize(target.width, target.height)
  }, size)

  // The breakpoint is a media query, so wait until the page reports the requested viewport.
  await expect
    .poll(
      () =>
        fixture!.page.evaluate(
          ({ height, width }) => Math.abs(window.innerWidth - width) <= 2 && Math.abs(window.innerHeight - height) <= 2,
          size
        ),
      { timeout: 10_000 }
    )
    .toBe(true)
}

// Below the breakpoint the nav sits in the sessions overlay. Ctrl+B is view.toggleSidebar.
async function revealNav(page: Page): Promise<void> {
  const nav = productNav(page)

  if (await nav.isVisible()) {
    return
  }

  await page.keyboard.press('Control+b')
  await expect(nav).toBeVisible({ timeout: 10_000 })
}

async function expandProjects(page: Page): Promise<void> {
  const projects = productNav(page).getByRole('button', { name: '项目', exact: true })

  if ((await projects.getAttribute('aria-expanded')) !== 'true') {
    await projects.click()
  }

  await expect(projects).toHaveAttribute('aria-expanded', 'true')
}

async function sendPlainChat(page: Page, text: string): Promise<void> {
  await productNav(page).getByRole('button', { name: '新建对话', exact: true }).click()

  const composer = page.getByRole('textbox', { name: '消息' })
  await composer.click()
  await composer.type(text, { delay: 20 })
  await page.keyboard.press('Enter')

  // Wait for the reply so the turn has finished before the next chat starts.
  await expect(page.getByText(MOCK_REPLY).last()).toBeVisible({ timeout: 60_000 })
}

async function expectOnlyPlainChats(page: Page): Promise<void> {
  const nav = productNav(page)
  await expandProjects(page)

  const projectSection = nav.getByLabel('项目', { exact: true })

  // The Home bucket holds the chats no folder claims. It is not a project, so no row names it.
  await expect(projectSection.getByRole('button', { name: /^(主页|Home)$/ })).toHaveCount(0)
  await expect(projectSection.getByText('暂无项目', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(projectSection.getByRole('button', { name: '新建项目', exact: true })).toBeVisible()

  // The plain chats are still listed under 对话, below its header. A session row is the button
  // named by its title and its 对话操作 menu, which keeps it apart from the transcript's copy.
  const header = page.getByRole('button', { name: '对话', exact: true })
  const sessionRow = (title: string) => page.getByRole('button', { name: new RegExp(`${title}.*对话操作`) })
  const firstRow = sessionRow(FIRST_CHAT)
  await expect(firstRow).toBeVisible({ timeout: 20_000 })
  await expect(sessionRow(SECOND_CHAT)).toBeVisible({ timeout: 20_000 })

  const headerBox = await header.boundingBox()
  const rowBox = await firstRow.boundingBox()
  expect(headerBox).not.toBeNull()
  expect(rowBox).not.toBeNull()
  expect(headerBox!.y).toBeLessThan(rowBox!.y)
}

test.beforeAll(async () => {
  fixture = await setupMockBackend({ seedSandbox: pinPlainChatCwd })
})

test.beforeEach(async ({}, testInfo) => {
  // The first wake of the profile can outlast the 90 s default, and hook time counts toward the test.
  testInfo.setTimeout(240_000)
  await waitForAppReady(fixture!, 180_000)
})

test.afterEach(async () => {
  // Close whatever a failed step left open, then hand the next test the wide window.
  const page = fixture!.page
  const settingsClose = page.getByRole('button', { name: '关闭设置', exact: true })

  if (await settingsClose.isVisible()) {
    await settingsClose.click()
  }

  const panelSlider = page.getByRole('slider', { name: '图片不透明度', exact: true })

  if (await panelSlider.isVisible()) {
    await page.keyboard.press('Escape')
  }

  await setWindowSize(WIDE)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('P0: plain chats never make the Home bucket a project row, and 新建项目 stays', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page

  await setWindowSize(WIDE)
  await revealNav(page)
  await sendPlainChat(page, FIRST_CHAT)
  await sendPlainChat(page, SECOND_CHAT)
  await expectOnlyPlainChats(page)
  await capture(page, `case1-plain-chats-${WIDE.name}`)

  await setWindowSize(NARROW)
  await revealNav(page)
  await expectOnlyPlainChats(page)
  await capture(page, `case1-plain-chats-${NARROW.name}`)
})

test('the 个性化 row opens the panel with the theme control and the image opacity slider', async () => {
  const page = fixture!.page

  for (const size of [WIDE, NARROW]) {
    await setWindowSize(size)
    await revealNav(page)
    await productNav(page).getByRole('button', { name: '个性化', exact: true }).click()

    await expect(page.getByRole('button', { name: '明亮', exact: true })).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('button', { name: '暗色', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '跟随系统', exact: true })).toBeVisible()
    await expect(page.getByRole('slider', { name: '图片不透明度', exact: true })).toBeVisible()
    await capture(page, `case2-personalization-${size.name}`)

    await page.keyboard.press('Escape')
    await expect(page.getByRole('slider', { name: '图片不透明度', exact: true })).toBeHidden()
  }
})

test('更多外观设置 in the panel opens Settings on the appearance tab', async () => {
  const page = fixture!.page

  await setWindowSize(WIDE)
  await revealNav(page)
  await productNav(page).getByRole('button', { name: '个性化', exact: true }).click()
  await page.getByRole('button', { name: '更多外观设置', exact: true }).click()

  await expect(page.getByRole('button', { name: '关闭设置', exact: true })).toBeVisible({ timeout: 15_000 })
  await capture(page, `case3-more-settings-${WIDE.name}`)

  // The interface-scale row belongs to the Appearance pane. A link that lands on another
  // settings tab shows that tab instead, so this row is the check that the right pane opened.
  await expect(page.getByText('界面缩放', { exact: true })).toBeVisible({ timeout: 15_000 })
})

test('the open page nav row carries aria-current="page"', async () => {
  const page = fixture!.page
  const nav = productNav(page)

  await setWindowSize(WIDE)
  await revealNav(page)

  const tasks = nav.getByRole('button', { name: '自动化', exact: true })
  const tools = nav.getByRole('button', { name: '工具', exact: true })

  await tasks.click()
  await expect(tasks).toHaveAttribute('aria-current', 'page')
  await expect(tools).not.toHaveAttribute('aria-current', 'page')
  await expect(nav.locator('button[aria-current="page"]')).toHaveCount(1)

  await tools.click()
  await expect(page.getByRole('textbox', { name: '搜索工具集…' })).toBeVisible({ timeout: 15_000 })
  await expect(tools).toHaveAttribute('aria-current', 'page')
  await expect(tasks).not.toHaveAttribute('aria-current', 'page')
  await expect(nav.locator('button[aria-current="page"]')).toHaveCount(1)
})
