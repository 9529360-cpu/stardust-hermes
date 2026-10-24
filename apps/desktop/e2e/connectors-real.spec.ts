import { expect, test } from './test'
import { setupMockBackend, waitForAppReady, type MockBackendFixture } from './fixtures'

// CX5 real acceptance (connectors.unified_dialog ON). The backend chain is
// REAL (electron → stardust serve → real MCP transports); only the LLM is the
// loopback mock, per spec. filesystem = local stdio via pinned npx package;
// deepwiki = remote HTTP MCP. No JSON editing anywhere in the flow.

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    extraConfig: 'connectors:\n  unified_dialog: true\n'
  })
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

test('filesystem (local stdio, pinned) connects via the dialog', async () => {
  const page = fixture!.page
  await openConnectors(page)

  // Catalog card present → open the connect dialog.
  await expect(page.getByText('filesystem', { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: 'D:/wt/logs/cx5-evidence/cx5-catalog.png' })
  await page.locator('[data-slot="connect-filesystem"]').click()

  // Detail dialog: publisher/permissions/runtime rows per spec.
  await expect(page.getByText('发布者', { exact: true })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('运行位置（本地/远程）', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'D:/wt/logs/cx5-evidence/cx5-add.png' })

  // Plain (non-secret) folder field, then connect. The real install writes
  // mcp_servers.filesystem + ALLOWED_DIR to .env, then probes the real server.
  await page.locator('[data-slot="connector-env-ALLOWED_DIR"]').fill('D:/wt/Temp/stardust/cx5/fs-allow')
  await page.locator('[data-slot="connector-connect"]').click()

  // Dialog closes on success; the connected list gains the row.
  await expect(page.locator('[data-slot="connector-connected-row"][data-server="filesystem"]')).toBeVisible({ timeout: 180_000 })
  await expect(page.getByText('可用工具:', { exact: false }).first()).toBeVisible({ timeout: 180_000 })
  await page.screenshot({ path: 'D:/wt/logs/cx5-evidence/cx5-filesystem-connected.png' })
  console.log('filesystem row connected:', await page
    .locator('[data-slot="connector-connected-row"][data-server="filesystem"]')
    .innerText())
})

test('deepwiki (remote http) connects via the dialog', async () => {
  const page = fixture!.page
  await openConnectors(page)

  await page.locator('[data-slot="connect-deepwiki"]').click()
  await expect(page.getByText('发布者', { exact: true })).toBeVisible({ timeout: 15_000 })
  // auth: none — no fields, straight to 连接.
  await page.locator('[data-slot="connector-connect"]').click()

  await expect(page.locator('[data-slot="connector-connected-row"][data-server="deepwiki"]')).toBeVisible({ timeout: 180_000 })
  await page.screenshot({ path: 'D:/wt/logs/cx5-evidence/cx5-real-connected.png' })
  console.log('deepwiki row connected:', await page
    .locator('[data-slot="connector-connected-row"][data-server="deepwiki"]')
    .innerText())
})
