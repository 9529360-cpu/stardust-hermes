import { expect, test } from './test'
import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

// CX5 opt-in connector catalog (connectors.unified_dialog). Flag OFF keeps the
// legacy Capabilities surface — that contract lives in the flag-off test below;
// these specs pin the flag-on catalog page and its connect dialog basics.

test.describe('connector catalog (connectors.unified_dialog)', () => {
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

  test('flag on renders the catalog page, not the legacy capability surface', async ({}, testInfo) => {
    const page = fixture!.page
    await page.evaluate(() => {
      window.location.hash = '#/connectors'
    })

    const catalog = page.locator('[data-slot="connector-catalog-page"]')
    await expect(catalog).toBeVisible({ timeout: 30_000 })
    // Chinese labels per spec: the catalog section and the add-connection CTA.
    await expect(page.getByText('服务目录').first()).toBeVisible()
    await expect(page.locator('[data-slot="add-connection"]')).toBeVisible()
    // The legacy CapabilitiesView header must NOT be the page anymore.
    await expect(page.locator('[data-slot="connector-catalog-search"]')).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('cx5-catalog-flag-on.png') })
  })

  test('flag off (default) keeps the legacy connectors surface', async () => {
    const sandboxFixture = await setupMockBackend()
    try {
      await waitForAppReady(sandboxFixture, 120_000)
      const page = sandboxFixture.page
      await page.evaluate(() => {
        window.location.hash = '#/connectors'
      })
      // Legacy surface renders the capabilities page; the CX5 catalog page
      // (code-split) must never mount.
      await expect(page.locator('[data-slot="connector-catalog-page"]')).toHaveCount(0, { timeout: 30_000 })
      await expect(page.locator('[data-slot="add-connection"]')).toHaveCount(0)
    } finally {
      await sandboxFixture.cleanup()
    }
  })
})
