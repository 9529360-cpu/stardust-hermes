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
  test('shows projects/threads, active thread and Review in a real workspace', async () => {
    const page = fixture!.page

    await expect(page.locator('[data-stardust-task-rail]')).toBeVisible()
    await expect(page.locator('[data-task-workspace]')).toBeVisible()

    // The mock backend exposes the checkout as a project. Enter it through the
    // same project-row affordance a user clicks so this screenshot exercises
    // real workspace routing / CWD ownership instead of a synthetic DOM seed.
    const projectRow = page.locator('[data-task-project-row]').first()
    await expect(projectRow).toBeVisible()
    await projectRow.hover()

    const newThreadInProject = projectRow.locator('button').first()
    await expect(newThreadInProject).toBeVisible()
    await newThreadInProject.click()

    await expect(page.locator('[data-task-header]')).not.toContainText('No project')
    await expect(page.locator('[data-tree-group="grp-review"]')).toBeVisible()

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

    // The discarded dashboard implementation must never leak back into the
    // product shell: Review is the right-side work surface now.
    await expect(page.locator('[data-personal-overview]')).toHaveCount(0)

    await expectVisualSnapshot(page, { name: 'stardust-codex-project-shell', app: fixture!.app })
  })
})
