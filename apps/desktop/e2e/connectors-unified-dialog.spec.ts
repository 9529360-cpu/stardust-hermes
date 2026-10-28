import type * as NodeHttp from 'node:http'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

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

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
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

  // eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
  test('catalog load failure is clear and retry recovers', async ({}, testInfo) => {
    test.setTimeout(240_000)

    const failureFixture = await setupMockBackend({
      extraConfig: 'connectors:\n  unified_dialog: true\n'
    })

    const { app, page } = failureFixture

    try {
      await waitForAppReady(failureFixture, 120_000)
      await app.evaluate(_electron => {
        const getBuiltinModule = process.getBuiltinModule

        if (typeof getBuiltinModule !== 'function') {
          throw new Error(`Node ${process.versions.node} does not expose process.getBuiltinModule`)
        }

        const http = getBuiltinModule('node:http') as typeof NodeHttp
        const syncBuiltinESMExports = getBuiltinModule('node:module').syncBuiltinESMExports

        const state = globalThis as typeof globalThis & {
          __connectorCatalogFault?: { fail: boolean; requests: number; restore: () => void }
        }

        const original = http.request

        const fault = {
          fail: true,
          requests: 0,
          restore: () => {
            http.request = original
            syncBuiltinESMExports()
            delete state.__connectorCatalogFault
          }
        }

        state.__connectorCatalogFault = fault
        http.request = ((...args: unknown[]) => {
          const url = args[0]

          if (url instanceof URL && url.pathname === '/api/mcp/catalog' && fault.fail) {
            fault.requests += 1
            const missing = new URL(url)
            missing.pathname = '/api/acceptance-missing-catalog'
            args[0] = missing
          }

          return Reflect.apply(original, http, args)
        }) as typeof original
        syncBuiltinESMExports()
      })

      await page.reload()

      await page.evaluate(() => {
        window.location.hash = '#/connectors'
      })

      const error = page.getByRole('heading', { name: '无法加载服务目录' })
      await expect(error).toBeVisible({ timeout: 30_000 })
      await expect(page.getByText('请检查后端连接后重试。')).toBeVisible()
      await expect(page.getByText('没有匹配的应用')).toHaveCount(0)
      await expect(page.locator('[data-slot="add-connection"]')).toBeVisible()
      await expect(page.locator('[data-slot="connector-connected-section"]')).toBeVisible()
      await expect
        .poll(() =>
          app.evaluate(
            () =>
              (globalThis as typeof globalThis & { __connectorCatalogFault?: { requests: number } })
                .__connectorCatalogFault?.requests ?? 0
          )
        )
        .toBeGreaterThan(0)
      await page.screenshot({ path: testInfo.outputPath('cx5-catalog-load-error.png') })

      await app.evaluate(() => {
        const state = globalThis as typeof globalThis & { __connectorCatalogFault?: { fail: boolean } }

        if (state.__connectorCatalogFault) {
          state.__connectorCatalogFault.fail = false
        }
      })
      await page.getByRole('button', { name: '重试', exact: true }).click()
      await expect(page.locator('[data-entry="deepwiki"]')).toBeVisible({ timeout: 30_000 })
      await expect(error).toHaveCount(0)
      await page.screenshot({ path: testInfo.outputPath('cx5-catalog-retry-recovered.png') })
    } finally {
      await app.evaluate(() => {
        const state = globalThis as typeof globalThis & { __connectorCatalogFault?: { restore: () => void } }
        state.__connectorCatalogFault?.restore()
      })
      await failureFixture.cleanup()
    }
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
