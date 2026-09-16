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
  test('shows projects/threads, active thread, Review and a bottom terminal deck', async () => {
    const page = fixture!.page

    await expect(page.locator('[data-stardust-task-rail]')).toBeVisible()
    await expect(page.locator('[data-task-workspace]')).toBeVisible()

    const projectRow = page.locator('[data-task-project-row]').first()
    await expect(projectRow).toBeVisible()
    await projectRow.hover()

    const newThreadInProject = projectRow.locator('button').first()
    await expect(newThreadInProject).toBeVisible()
    await newThreadInProject.click()

    const taskHeader = page.locator('[data-task-header]')
    await expect(taskHeader).not.toContainText('No project')
    await expect(taskHeader).toContainText('New thread')
    await expect(page.getByText('Project workspace', { exact: true })).toBeVisible()
    await expect(page.getByText('What should we change?', { exact: true })).toBeVisible()
    await expect(page.locator('[data-project-thread-intro] .assistant-home__mark')).toHaveCount(0)
    await expect(page.locator('[data-slot="composer-rich-input"]')).toHaveAttribute('data-placeholder', 'What are we building?')
    await expect(page.locator('[data-tree-group="grp-review"]')).toBeVisible()

    const review = page.locator('aside[aria-label="Review"]')
    await expect(review).toBeVisible()
    await expect(review.locator('[data-review-workspace-header]')).toBeVisible()

    const reviewBounds = await review.boundingBox()
    expect(reviewBounds?.width ?? 0).toBeGreaterThanOrEqual(315)

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

      const diffPanel = review.locator('[data-review-diff-panel]')
      await expect(diffPanel).toBeVisible()
      const diffBounds = await diffPanel.boundingBox()
      expect(diffBounds?.height ?? 0).toBeGreaterThan(180)

      for (const groupId of ['grp-sessions', 'grp-main', 'grp-review']) {
        const visibleHeaders = await page.locator(`[data-tree-group="${groupId}"] [data-panel-header]`).evaluateAll(nodes =>
          nodes.filter(node => getComputedStyle(node).display !== 'none').length
        )

        expect(visibleHeaders).toBe(0)
      }

      await expect(page.locator('[data-task-workspace] [data-tour="composer"]')).toHaveCount(1)
      await expect(page.locator('[data-personal-overview]')).toHaveCount(0)

      await expectVisualSnapshot(page, { name: 'stardust-codex-project-shell', app: fixture!.app })

      // Terminal is a real, resizable bottom work surface. Opening it from the
      // thread header must not replace Review or turn into another right rail.
      const terminalButton = taskHeader.getByRole('button', { name: 'Terminal' })
      await expect(terminalButton).toBeVisible()
      await terminalButton.click()

      const terminalSlot = page.locator('[data-terminal-slot]')
      await expect(terminalSlot).toBeVisible({ timeout: 20_000 })
      await expect(page.locator('[data-persistent-terminal] .xterm')).toBeVisible({ timeout: 20_000 })
      await expect(review).toBeVisible()

      const workspaceBounds = await page.locator('[data-task-workspace]').boundingBox()
      const terminalBounds = await terminalSlot.boundingBox()
      expect(terminalBounds?.y ?? 0).toBeGreaterThan((workspaceBounds?.y ?? 0) + (workspaceBounds?.height ?? 0) * 0.52)

      await expectVisualSnapshot(page, { name: 'stardust-codex-terminal-deck', app: fixture!.app })
    } finally {
      fs.writeFileSync(SMOKE_DIFF_PATH, originalSmokeFile, 'utf8')
    }
  })
})
