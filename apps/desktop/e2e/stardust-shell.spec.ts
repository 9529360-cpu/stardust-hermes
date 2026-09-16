import * as fs from 'node:fs'
import * as path from 'node:path'

import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expectVisualSnapshot } from './visual-snapshot'

const REPO_ROOT = path.resolve(import.meta.dirname, '..', '..', '..')
const SMOKE_DIFF_PATH = path.join(REPO_ROOT, 'STARDUST.md')
const SMOKE_DIFF_MARKER = 'stardust visual smoke diff'

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
  test('shows projects/threads, active thread and a readable real Review diff', async () => {
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
    await expect(page.getByText('Project workspace', { exact: true })).toBeVisible()
    await expect(page.getByText('What should we change?', { exact: true })).toBeVisible()
    await expect(page.locator('[data-tree-group="grp-review"]')).toBeVisible()

    const review = page.locator('aside[aria-label="Review"]')
    await expect(review).toBeVisible()

    // A blank "No diffs" rail proves only layout. Create one reversible change
    // in the real checkout, explicitly refresh Review, and open the real diff so
    // the screenshot validates the working Codex review surface end to end.
    const originalSmokeFile = fs.readFileSync(SMOKE_DIFF_PATH, 'utf8')

    try {
      fs.writeFileSync(SMOKE_DIFF_PATH, `${originalSmokeFile}\n<!-- ${SMOKE_DIFF_MARKER} -->\n`, 'utf8')

      const refreshReview = review.locator('button:has(.codicon-refresh)')
      await expect(refreshReview).toBeVisible()
      await refreshReview.click()

      const changedFile = review.getByText('STARDUST.md', { exact: true }).first()
      await expect(changedFile).toBeVisible({ timeout: 15_000 })
      await changedFile.click()
      await expect(review).toContainText(SMOKE_DIFF_MARKER, { timeout: 15_000 })

      // Text existing in the DOM is not enough. This caught the prior flex bug
      // where FileDiffPanel mounted inside a zero-height parent and only its
      // title strip was visible to the user.
      const diffPanel = review.locator('[data-review-diff-panel]')
      await expect(diffPanel).toBeVisible()
      const diffBounds = await diffPanel.boundingBox()
      expect(diffBounds?.height ?? 0).toBeGreaterThan(180)

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
    } finally {
      fs.writeFileSync(SMOKE_DIFF_PATH, originalSmokeFile, 'utf8')
    }
  })
})
