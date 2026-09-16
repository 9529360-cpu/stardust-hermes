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

test.describe('Stardust Codex desktop shell', () => {
  test('boots into projects/threads, active thread and Review', async () => {
    const page = fixture!.page

    await expect(page.locator('[data-stardust-task-rail]')).toBeVisible()
    await expect(page.locator('[data-task-workspace]')).toBeVisible()
    await expect(page.locator('[data-tree-group="grp-review"]')).toHaveCount(1)

    // The three permanent product regions are not generic IDE tab stacks.
    for (const groupId of ['grp-sessions', 'grp-main', 'grp-review']) {
      const visibleHeaders = await page.locator(`[data-tree-group="${groupId}"] [data-panel-header]`).evaluateAll(nodes =>
        nodes.filter(node => getComputedStyle(node).display !== 'none').length
      )

      expect(visibleHeaders).toBe(0)
    }

    // One real chat composer only. The failed skin rendered the old title
    // editor as a second giant field above the transcript.
    await expect(page.locator('[data-task-workspace] [data-tour="composer"]')).toHaveCount(1)

    // The discarded dashboard implementation must not leak back into the
    // product shell: Review is the right-side work surface now.
    await expect(page.locator('[data-personal-overview]')).toHaveCount(0)

    await expectVisualSnapshot(page, { name: 'stardust-codex-shell-ready', app: fixture!.app })
  })
})
