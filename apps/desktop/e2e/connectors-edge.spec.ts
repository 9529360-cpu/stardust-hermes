import { expect, test } from './test'
import { setupMockBackend, waitForAppReady, type MockBackendFixture } from './fixtures'

// CX5 edge cases (connectors.unified_dialog ON, real backend, mock LLM):
// duplicate name, missing command, cancel, API-key branch, custom URL/command.
// Runs on a fresh sandbox; filesystem connect here doubles as the duplicate
// setup. Chinese error/state text per spec.

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({ extraConfig: 'connectors:\n  unified_dialog: true\n' })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

async function openConnectors(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    window.location.hash = '#/connectors'
  })
  await expect(page.locator('[data-slot="connector-catalog-page"]')).toBeVisible({ timeout: 30_000 })
}

test('cancel leaves config unchanged', async ({ page: _p }) => {
  const page = fixture!.page
  await openConnectors(page)
  await page.locator('[data-slot="connect-deepwiki"]').click()
  await expect(page.getByText('发布者', { exact: true })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByText('发布者', { exact: true })).toHaveCount(0)
  // No connected row appeared.
  await expect(page.locator('[data-slot="connector-connected-row"][data-server="deepwiki"]')).toHaveCount(0)
})

test('duplicate name is rejected in Chinese', async () => {
  const page = fixture!.page
  await openConnectors(page)
  // Connect filesystem first (real install).
  await page.locator('[data-slot="connect-filesystem"]').click()
  await page.locator('[data-slot="connector-env-ALLOWED_DIR"]').fill('D:/wt/Temp/stardust/cx5/fs-allow')
  await page.locator('[data-slot="connector-connect"]').click()
  await expect(page.locator('[data-slot="connector-connected-row"][data-server="filesystem"]')).toBeVisible({ timeout: 180_000 })

  // Now the custom dialog with the same name → duplicate error, no crash.
  await page.locator('[data-slot="add-connection"]').click()
  await page.locator('[data-slot="custom-name"]').fill('filesystem')
  await page.locator('[data-slot="custom-command"]').fill('npx -y @modelcontextprotocol/server-filesystem@2026.7.10 D:/wt/Temp/stardust/cx5/fs-allow')
  await page.locator('[data-slot="custom-connect"]').click()
  await expect(page.getByText('已存在同名连接 filesystem。')).toBeVisible({ timeout: 15_000 })
})

test('missing command/url is rejected in Chinese', async () => {
  const page = fixture!.page
  await openConnectors(page)
  await page.locator('[data-slot="add-connection"]').click()
  await page.locator('[data-slot="custom-name"]').fill('empty-service')
  await page.locator('[data-slot="custom-connect"]').click()
  await expect(page.getByText('请输入服务 URL 或命令。')).toBeVisible({ timeout: 15_000 })
})

test('missing name is rejected in Chinese', async () => {
  const page = fixture!.page
  await openConnectors(page)
  await page.locator('[data-slot="add-connection"]').click()
  await page.locator('[data-slot="custom-command"]').fill('npx -y some-server')
  await page.locator('[data-slot="custom-connect"]').click()
  await expect(page.getByText('请输入连接名称。')).toBeVisible({ timeout: 15_000 })
})

test('custom URL + API key branch: missing key rejected, then connects', async () => {
  const page = fixture!.page
  await openConnectors(page)
  await page.locator('[data-slot="add-connection"]').click()
  await page.locator('[data-slot="custom-name"]').fill('echo-api')
  await page.locator('[data-slot="custom-url"]').fill('https://mcp.deepwiki.com/mcp')
  await page.locator('[data-slot="custom-use-api-key"]').click()
  await page.locator('[data-slot="custom-connect"]').click()
  await expect(page.getByText('请输入此服务的 API 密钥。')).toBeVisible({ timeout: 15_000 })

  // Provide a dummy key — the connect itself succeeds (config + secure store);
  // the real endpoint may 401 but that is a probe-state, not a crash.
  await page.locator('[data-slot="custom-api-key"]').fill('cx5-e2e-dummy-key')
  await page.locator('[data-slot="custom-connect"]').click()
  await expect(page.locator('[data-slot="connector-connected-row"][data-server="echo-api"]')).toBeVisible({ timeout: 60_000 })
})
