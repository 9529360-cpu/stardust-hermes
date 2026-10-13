/**
 * The assistant connects the user's own Chrome itself. Owner field case 2026-10-12: with no such path the
 * assistant could only tell the owner to open Tools, install the extension and press Connect, which took an
 * hour of back-and-forth in chat.
 *
 * The mocked model calls the real `browser` tool with action=connect. The real backend asks once through
 * the approval card, emits `browser.host.connect`, and the desktop runs the same pairing path as the Tools
 * page's Connect Chrome button for the chat on screen.
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import { CHROME_CONNECT_REPLY, CHROME_CONNECT_TRIGGER } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const NARROW_WIDTH = 560

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    // Manual approvals: the pairing must be the user's one explicit yes, shown as a real card.
    extraConfig: 'auxiliary:\n  title_generation:\n    enabled: false\napprovals:\n  mode: "manual"'
  })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
})

// Playwright requires its fixture argument to use object destructuring.
// eslint-disable-next-line no-empty-pattern
test('the assistant asks once, then the desktop pairs Chrome to the chat on screen', async ({}, testInfo) => {
  test.setTimeout(180_000)
  const page = fixture!.page
  const composer = page.locator('[contenteditable="true"]').first()
  await composer.waitFor({ state: 'visible', timeout: 10_000 })
  await composer.click()
  await composer.fill(CHROME_CONNECT_TRIGGER)
  await page.keyboard.press('Enter')

  const approval = page.locator('[data-slot="tool-approval-inline"], [data-slot="tool-approval-actions"]').first()
  await expect(approval).toBeVisible({ timeout: 60_000 })
  await page.screenshot({ path: testInfo.outputPath('chrome-connect-approval.png') })
  await approval.getByRole('button').first().click()

  // The desktop says what is happening on screen before the bridge reports back.
  await expect(page.getByText('正在连接你的 Chrome', { exact: true })).toBeVisible({ timeout: 20_000 })
  await page.screenshot({ path: testInfo.outputPath('chrome-connect-toast.png') })

  const transcript = page.locator('[data-slot="aui_thread-viewport"]')
  await expect(transcript).toContainText(CHROME_CONNECT_REPLY, { timeout: 60_000 })

  // Same pairing as the Connect Chrome button: the Tools panel for this chat shows the bridge running.
  await page.locator('[data-personal-product-nav]').getByRole('button', { name: '工具', exact: true }).click()
  await page.getByText('浏览器自动化', { exact: true }).first().click()
  const disconnect = page.getByRole('button', { name: '断开连接', exact: true })
  await expect(disconnect).toBeEnabled({ timeout: 25_000 })
  await page.screenshot({ path: testInfo.outputPath('chrome-connect-tools-panel.png') })

  await fixture!.app.evaluate(({ BrowserWindow }, width) => {
    BrowserWindow.getAllWindows()[0].setSize(width, 820)
  }, NARROW_WIDTH)
  await page.waitForTimeout(600)
  await page.screenshot({ path: testInfo.outputPath('chrome-connect-tools-panel-narrow.png') })

  await disconnect.click()
  await expect(page.getByRole('button', { name: '连接 Chrome', exact: true })).toBeEnabled({ timeout: 15_000 })
})
