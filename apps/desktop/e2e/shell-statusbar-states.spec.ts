/**
 * The status bar reads the same everywhere. The approvals item shows the configured
 * mode once it is read, never a default before that; no readout with nothing to show
 * renders a bare dash; and the gateway item stays on the agent space.
 *
 * SHELL_STATES_SCREENSHOT_DIR=<dir> saves captures for design review; never part of the
 * assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// The mock sandbox's config.yaml sets approvals.mode to "off", so that is the mode to read back.
const CONFIGURED_MODE = /关闭/
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

async function approvalText(page: MockBackendFixture['page']): Promise<string> {
  return page.evaluate(() =>
    (document.querySelector('[data-statusbar-item="approval-mode"]')?.textContent ?? '').replace(/\s+/g, ' ').trim()
  )
}

async function dashItemCount(page: MockBackendFixture['page']): Promise<number> {
  return page.evaluate(
    () =>
      Array.from(document.querySelectorAll('[data-slot="statusbar"] [data-statusbar-item]')).filter(
        el => (el.textContent ?? '').trim() === '—'
      ).length
  )
}

/** Picks a nav item. Below the sidebar breakpoint the nav lives in the sessions overlay, so open it first. */
async function goTo(page: MockBackendFixture['page'], name: RegExp, narrow: boolean): Promise<void> {
  const item = page.getByRole('button', { name }).first()

  if (narrow && !(await item.isVisible().catch(() => false))) {
    await page.keyboard.press('Control+b')
    await expect(item).toBeVisible({ timeout: 10_000 })
  }

  await item.click()
  await page.waitForTimeout(400)
}

async function setWindowSize(page: MockBackendFixture['page'], width: number, height: number): Promise<void> {
  await fixture!.app.evaluate(
    ({ BrowserWindow }, size) => {
      BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
    },
    { width, height }
  )
  await page.waitForTimeout(600)
}

async function expectStatusBarAcrossPages(page: MockBackendFixture['page'], widthName: string, narrow: boolean) {
  await capture(page, `${widthName}-composer`)
  await expect.poll(() => approvalText(page), { timeout: 60_000 }).toMatch(CONFIGURED_MODE)
  expect(await dashItemCount(page)).toBe(0)

  await goTo(page, /^(工具|Tools)$/, narrow)
  await expect.poll(() => approvalText(page)).toMatch(CONFIGURED_MODE)
  await capture(page, `${widthName}-tools`)

  await goTo(page, /^(智能体空间|Agent space)$/, narrow)
  await expect(page.locator('[data-slot="bots-roster"]')).toBeVisible({ timeout: 20_000 })
  await expect.poll(() => approvalText(page)).toMatch(CONFIGURED_MODE)
  // The gateway item is the same on every page, the agent space included.
  await expect(page.locator('[data-statusbar-item="gateway-health"]')).toBeVisible()
  await capture(page, `${widthName}-agent-space`)

  await goTo(page, /^(任务|Tasks)$/, narrow)
  await expect.poll(() => approvalText(page)).toMatch(CONFIGURED_MODE)
  expect(await dashItemCount(page)).toBe(0)
  await capture(page, `${widthName}-tasks`)
}

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('the approvals item never shows a default mode before the configured one is read', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  const seen: string[] = []
  const deadline = Date.now() + 90_000
  let settled = ''

  while (Date.now() < deadline) {
    const text = (await approvalText(page)) || '(absent)'

    if (text !== seen[seen.length - 1]) {
      seen.push(text)
    }

    if (CONFIGURED_MODE.test(text)) {
      settled = text

      break
    }

    await page.waitForTimeout(50)
  }

  await waitForAppReady(fixture!, 120_000)

  expect(seen.join(' | ')).not.toMatch(/智能/)
  expect(settled).toMatch(/审批/)
  expect(settled).toMatch(CONFIGURED_MODE)
})

test('at 1220x800 the status bar reads the same on every page', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await expectStatusBarAcrossPages(page, '1220x800', false)
})

test('at 560x820 the status bar reads the same on every page', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  // The previous test leaves the Tasks page open, which has no composer to wait for.
  await page.getByRole('button', { name: /^(新建对话|New chat)$/ }).first().click()
  await page.waitForSelector('textarea, [contenteditable="true"]', { state: 'attached', timeout: 30_000 })
  await waitForAppReady(fixture!, 120_000)

  await setWindowSize(page, NARROW_WIDTH, NARROW_HEIGHT)
  await goTo(page, /^(新建对话|New chat)$/, true)
  await expectStatusBarAcrossPages(page, '560x820', true)
})
