import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('the bottom usage entry opens without restoring the retired right overview', async () => {
  const page = fixture!.page
  const statusbar = page.locator('[data-slot="statusbar"]')
  await expect(statusbar).toBeVisible()
  // A fresh draft has no usage to report, so the bottom entry is not rendered until a turn has run (below).
  await expect(statusbar.locator('[data-statusbar-item="context-usage"]')).toHaveCount(0)

  const toggle = page.locator('[data-tour="right-pane-toggle"]')
  await toggle.click()
  await expect(page.locator('[data-personal-overview]')).toHaveCount(0)
  await expect(page.getByRole('tab', { name: '上下文' })).toHaveCount(0)
  const composer = page.locator('[contenteditable="true"]').first()
  await expect(composer).toBeVisible()
  await composer.fill('Hello, can you hear me?')
  await composer.press('Enter')
  await expect(page.getByText('Hello from the mock inference server!', { exact: false })).toBeVisible({ timeout: 60_000 })

  await statusbar.locator('[data-statusbar-item="context-usage"]').click()
  const sessionUsage = page.locator('[data-slot="session-token-usage"]')
  await expect(sessionUsage.locator('dl')).toBeVisible({ timeout: 15_000 })
  await expect(sessionUsage.getByText('缓存读取')).toBeVisible()
  await expect(sessionUsage.getByText('缓存写入')).toBeVisible()
})
