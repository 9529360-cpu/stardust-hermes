/**
 * Regression coverage for returning to a working session as its task panel
 * expands. The transcript must reconcile to the composer's full measured
 * height without needing a manual scroll to repair the position.
 */

import { TASK_PANEL_RESUME_TRIGGER } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, type Page, test } from './test'

const SURFACE = '[data-composer-target]:visible'
const PROMPT = `${TASK_PANEL_RESUME_TRIGGER}: keep the task panel expanded while this session is reopened.`

function activeSurface(page: Page) {
  return page.locator(SURFACE).last()
}

async function send(page: Page, text: string): Promise<void> {
  const composer = activeSurface(page).locator('[contenteditable="true"]').first()

  await composer.waitFor({ state: 'visible', timeout: 15_000 })
  await composer.click()
  await composer.type(text, { delay: 5 })
  await page.keyboard.press('Enter')
}

async function openFreshDraft(page: Page): Promise<void> {
  await page.locator('[data-tour="sidebar-nav-new-session"]').click()
  await expect(activeSurface(page).locator('[data-slot="aui_thread-viewport"]')).not.toContainText(PROMPT)
  await page.waitForTimeout(1_000)
}

async function reopenWorkingSession(page: Page): Promise<void> {
  const sidebar = page.locator('[data-slot="sidebar"]')
  const row = sidebar.locator('[data-working="true"]').first()

  await row.waitFor({ state: 'visible', timeout: 30_000 })
  await row.click()
  await expect(activeSurface(page).locator('[data-slot="aui_thread-viewport"]')).toContainText(
    'Task-panel clearance line 24',
    { timeout: 30_000 },
  )
}

interface ClearanceMetrics {
  composerHeight: number
  distanceFromBottom: number
  latestAssistantBottom: number
  statusPanelTop: number
  viewportHeight: number
}

async function clearanceMetrics(page: Page): Promise<ClearanceMetrics> {
  return activeSurface(page).evaluate(surface => {
    const chatSurface = surface.closest<HTMLElement>('[data-chat-surface]')!
    const viewport = surface.querySelector<HTMLElement>('[data-slot="aui_thread-viewport"]')!
    const latest = Array.from(surface.querySelectorAll<HTMLElement>('[data-role="assistant"]')).at(-1)!
    const status = surface.querySelector<HTMLElement>('[data-slot="composer-status-stack"]')!
    const styles = getComputedStyle(chatSurface)

    return {
      composerHeight: Number.parseFloat(styles.getPropertyValue('--composer-measured-height')),
      distanceFromBottom: viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop,
      latestAssistantBottom: latest.getBoundingClientRect().bottom,
      statusPanelTop: status.getBoundingClientRect().top,
      viewportHeight: viewport.clientHeight,
    }
  })
}

async function resizeWindow(fixture: MockBackendFixture, width: number, height: number): Promise<void> {
  await fixture.app.evaluate(
    ({ BrowserWindow }, size) => BrowserWindow.getAllWindows()[0].setSize(size.width, size.height),
    { width, height },
  )
  await expect.poll(() => fixture.page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }))).toEqual({
    width,
    height,
  })
}

async function restoreWindowFocus(fixture: MockBackendFixture): Promise<void> {
  await fixture.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].blur())
  await expect.poll(() => fixture.page.evaluate(() => document.hasFocus())).toBe(false)
  await fixture.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].focus())
  await expect.poll(() => fixture.page.evaluate(() => document.hasFocus())).toBe(true)
}

test.describe('working-session task-panel clearance', () => {
  let fixture: MockBackendFixture | null = null

  test.beforeEach(async () => {
    fixture = await setupMockBackend({
      mockServer: { holdFirstCompletionContaining: TASK_PANEL_RESUME_TRIGGER },
    })
    await waitForAppReady(fixture, 120_000)
  })

  test.afterEach(async () => {
    await fixture?.cleanup()
    fixture = null
  })

  // eslint-disable-next-line no-empty-pattern -- Playwright needs the object pattern here
  test('real window focus keeps a reopened working session clear at supported sizes', async ({}, testInfo) => {
    const page = fixture!.page

    await send(page, PROMPT)
    await fixture!.mock.waitForHeldCompletion()
    await openFreshDraft(page)

    // Re-open while the long response is still streaming. Its todo call lands
    // afterward, so the already-visible composer grows only after the initial
    // session-load scroll settle has finished.
    fixture!.mock.releaseHeldStream()
    await page.waitForTimeout(1_000)
    await reopenWorkingSession(page)
    await expect(activeSurface(page).getByText(/Tasks 1\/5|任务 1\/5/)).toBeVisible({ timeout: 30_000 })

    const taskPanel = activeSurface(page)
      .locator('[data-slot="composer-status-stack"]')
      .getByRole('button', { name: /Tasks 1\/5|任务 1\/5/ })

    const latestAssistant = activeSurface(page).locator('[data-role="assistant"]').last()
    const activeCommand = latestAssistant.locator('[data-tool-group] [data-tool-ticker]')

    await expect(taskPanel).toHaveAttribute('aria-expanded', 'true')

    // Exercise the real status-section layout transition. The previous test
    // inserted a spacer into the scroll viewport and expected stale geometry;
    // the production stick-to-bottom behavior immediately corrected it. The
    // long streamed paragraph is retained in history and may be skipped by
    // content-visibility, so verify the live terminal status in the newest
    // assistant message after the focus recovery edge instead.
    await taskPanel.click()
    await expect(taskPanel).toHaveAttribute('aria-expanded', 'false')
    await taskPanel.click()
    await expect(taskPanel).toHaveAttribute('aria-expanded', 'true')

    for (const size of [
      { name: 'wide', width: 1220, height: 800 },
      { name: 'compact', width: 960, height: 680 },
    ]) {
      await resizeWindow(fixture!, size.width, size.height)
      await restoreWindowFocus(fixture!)

      await expect.poll(async () => {
        const metrics = await clearanceMetrics(page)

        return metrics.distanceFromBottom <= 1 && metrics.latestAssistantBottom <= metrics.statusPanelTop
      }).toBe(true)

      const metrics = await clearanceMetrics(page)

      await page.screenshot({ path: testInfo.outputPath(`task-panel-after-resume-${size.name}.png`) })
      expect(metrics.composerHeight, JSON.stringify({ ...metrics, size })).toBeGreaterThanOrEqual(190)
      expect(metrics.distanceFromBottom, JSON.stringify({ ...metrics, size })).toBeLessThanOrEqual(1)
      expect(metrics.latestAssistantBottom, JSON.stringify({ ...metrics, size })).toBeLessThanOrEqual(metrics.statusPanelTop)
      await expect(activeCommand).toBeVisible()
      await expect(activeCommand).toContainText(/sleep 60/)
    }
  })
})
