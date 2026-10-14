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
  const row = sidebar.getByRole('button', { name: /(?:Chat running|Needs your input|Working|对话运行中|需要.*输入|正在工作)/ }).first()

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
  latestMessageBottom: number
  statusPanelTop: number
  viewportHeight: number
}

interface StaleClearanceState {
  distance: number
  following: string | undefined
  latestMessageBottom: number
  overflowAnchor: string
  scrollHeightAfter: number
  scrollHeightBefore: number
  scrollTopAfter: number
  scrollTopBefore: number
  spacerHeight: number
  statusPanelTop: number
  visibility: DocumentVisibilityState
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
      latestMessageBottom: latest.getBoundingClientRect().bottom,
      statusPanelTop: status.getBoundingClientRect().top,
      viewportHeight: viewport.clientHeight,
    }
  })
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
  test('window focus reanchors a working session above the expanded task panel', async ({}, testInfo) => {
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

    // Reproduce the stale geometry at the foreground boundary. Active turns
    // disable Chromium's background throttling, so visibility can stay `visible`
    // and window focus is the only foreground edge that can repair it.
    await page.waitForTimeout(750)

    const viewport = activeSurface(page).locator('[data-slot="aui_thread-viewport"]')
    await expect(viewport).toHaveAttribute('data-following', 'true')

    const staleState = await viewport.evaluate((viewport): StaleClearanceState => {
      // Grow scrollHeight before the observed thread-content node. Keep the
      // injected spacer rigid and at least one viewport tall: a fixed 160px
      // spacer can fit inside the otherwise empty viewport on Linux, creating
      // no scroll range. Disable native anchoring so this remains a layout
      // change, not a synthetic user scroll that escapes the lock.
      viewport.style.setProperty('overflow-anchor', 'none')
      const staleClearanceHeight = Math.max(320, viewport.clientHeight)
      const staleClearance = document.createElement('div')
      staleClearance.style.height = `${staleClearanceHeight}px`
      staleClearance.style.minHeight = `${staleClearanceHeight}px`
      staleClearance.style.flex = `0 0 ${staleClearanceHeight}px`
      staleClearance.setAttribute('aria-hidden', 'true')
      const scrollHeightBefore = viewport.scrollHeight
      const scrollTopBefore = viewport.scrollTop
      viewport.prepend(staleClearance)

      const scrollHeightAfter = viewport.scrollHeight
      const scrollTopAfter = viewport.scrollTop
      const distance = scrollHeightAfter - viewport.clientHeight - scrollTopAfter
      const surface = viewport.closest<HTMLElement>('[data-composer-target]')!
      const latest = Array.from(surface.querySelectorAll<HTMLElement>('[data-role="assistant"]')).at(-1)!
      const status = surface.querySelector<HTMLElement>('[data-slot="composer-status-stack"]')!
      const following = viewport.dataset.following
      const latestMessageBottom = latest.getBoundingClientRect().bottom
      const statusPanelTop = status.getBoundingClientRect().top

      window.dispatchEvent(new Event('focus'))

      return {
        distance,
        following,
        latestMessageBottom,
        overflowAnchor: getComputedStyle(viewport).overflowAnchor,
        scrollHeightAfter,
        scrollHeightBefore,
        scrollTopAfter,
        scrollTopBefore,
        spacerHeight: staleClearance.getBoundingClientRect().height,
        statusPanelTop,
        visibility: document.visibilityState,
        viewportHeight: viewport.clientHeight
      }
    })

    expect(staleState.visibility, JSON.stringify(staleState)).toBe('visible')
    expect(staleState.following, JSON.stringify(staleState)).toBe('true')
    expect(staleState.overflowAnchor, JSON.stringify(staleState)).toBe('none')
    expect(staleState.spacerHeight, JSON.stringify(staleState)).toBeGreaterThanOrEqual(staleState.viewportHeight)
    expect(
      staleState.scrollHeightAfter - staleState.scrollHeightBefore,
      JSON.stringify(staleState),
    ).toBeGreaterThan(100)
    expect(staleState.distance, JSON.stringify(staleState)).toBeGreaterThan(100)
    expect(staleState.latestMessageBottom, JSON.stringify(staleState)).toBeGreaterThan(staleState.statusPanelTop)
    await page.waitForTimeout(1_000)
    const metrics = await clearanceMetrics(page)
    await page.screenshot({ path: testInfo.outputPath('task-panel-after-resume.png') })

    expect(metrics.composerHeight, JSON.stringify(metrics)).toBeGreaterThanOrEqual(190)
    expect(metrics.distanceFromBottom, JSON.stringify(metrics)).toBeLessThan(staleState.distance / 2)
    expect(metrics.latestMessageBottom, JSON.stringify(metrics)).toBeLessThanOrEqual(metrics.statusPanelTop)
  })
})
