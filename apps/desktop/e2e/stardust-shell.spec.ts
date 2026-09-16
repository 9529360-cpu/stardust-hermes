import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expectVisualSnapshot } from './visual-snapshot'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test.describe('Stardust desktop shell', () => {
  test('boots into the task-first three-region product shell', async () => {
    const page = fixture!.page

    await expect(page.locator('[data-stardust-task-rail]')).toBeVisible()
    await expect(page.locator('[data-task-workspace]')).toBeVisible()
    await expect(page.locator('[data-personal-overview]')).toBeVisible()

    // The permanent product regions must not expose the old IDE tab chrome.
    for (const groupId of ['grp-sessions', 'grp-main', 'grp-context']) {
      const visibleHeaders = await page.locator(`[data-tree-group="${groupId}"] [data-panel-header]`).evaluateAll(nodes =>
        nodes.filter(node => getComputedStyle(node).display !== 'none').length
      )

      expect(visibleHeaders).toBe(0)
    }

    // One task composer only. The failed skin rendered the legacy title editor
    // as a second giant white field above the transcript.
    await expect(page.locator('[data-task-workspace] [data-tour="composer"]')).toHaveCount(1)

    await expectVisualSnapshot(page, { name: 'stardust-shell-ready', app: fixture!.app })
  })
})
