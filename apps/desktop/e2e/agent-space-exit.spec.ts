/**
 * Regression: opening the agent space must not trap the user. The roster is a tab in the sessions zone and
 * the product nav hides that zone's tab strip, so the back control in the roster header is the only way to
 * the nav. Clicking it must bring the nav back.
 */
import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test.describe('agent space exit', () => {
  test('the back control in the roster returns to the product nav', async () => {
    const page = fixture!.page
    await waitForAppReady(fixture!, 120_000)

    await page.getByRole('button', { name: /智能体空间|Agent space/ }).click()
    const back = page.getByRole('button', { name: /返回对话|Back to conversations/ })
    await expect(back).toBeVisible({ timeout: 20_000 })

    await back.click()
    await expect(page.getByRole('button', { name: /新建对话|New chat/ }).first()).toBeVisible({ timeout: 10_000 })
  })
})
