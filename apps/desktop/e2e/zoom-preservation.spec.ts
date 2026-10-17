/**
 * E2E regression: in-page route navigation must preserve the chosen UI scale.
 *
 * Desktop is a HashRouter over one file:// document, so every route is a
 * distinct URL to Chromium's per-URL zoom store, and a route with no record of
 * its own resolves to the host default (100%). In-page navigation fires no load
 * or window event, so nothing re-asserted the persisted level: switching
 * sessions dropped the window to 100% while Appearance kept reading the chosen
 * scale (#48658, #38854, #79863).
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const SCALE = 110

let fixture: MockBackendFixture | null = null

async function readZoomPercent(): Promise<number> {
  return fixture!.page.evaluate(async () => {
    const desktop = window as unknown as {
      stardustDesktop: { zoom: { get: () => Promise<{ percent: number }> } }
    }

    return (await desktop.stardustDesktop.zoom.get()).percent
  })
}

async function setZoomPercent(percent: number): Promise<void> {
  await fixture!.page.evaluate(target => {
    const desktop = window as unknown as {
      stardustDesktop: { zoom: { setPercent: (percent: number) => void } }
    }

    desktop.stardustDesktop.zoom.setPercent(target)
  }, percent)
  await expect.poll(readZoomPercent).toBe(percent)
}

async function openSettings(): Promise<void> {
  const page = fixture!.page
  const closeSettings = page.getByRole('button', { name: /close settings|关闭设置/i })

  if (await closeSettings.isVisible()) {
    return
  }

  await Promise.all([
    page.waitForURL(url => url.hash.startsWith('#/settings')),
    page.getByRole('button', { name: /open settings|打开设置/i }).click()
  ])
  await expect(closeSettings).toBeVisible()
}

async function openAppearanceSettings(): Promise<void> {
  const page = fixture!.page
  const appearanceNavigation = page.getByRole('complementary').locator('[data-tour="nav-config:appearance"]')

  await expect(appearanceNavigation).toBeVisible()
  await Promise.all([
    page.waitForURL(url => {
      if (!url.hash.startsWith('#/settings?')) {
        return false
      }

      const query = url.hash.slice(url.hash.indexOf('?') + 1)

      return new URLSearchParams(query).get('tab') === 'config:appearance'
    }),
    appearanceNavigation.click()
  ])
  await expect(appearanceNavigation).toHaveClass(/bg-\(--ui-bg-tertiary\)/)
}

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('a non-default UI scale survives navigation to never-zoomed routes', async () => {
  await setZoomPercent(SCALE)

  // Exercise the actual React Router controls. Settings and its Appearance
  // tab are distinct hash URLs that have not been zoomed earlier in this test.
  await openSettings()
  await expect.poll(readZoomPercent, { message: 'UI scale after opening Settings' }).toBe(SCALE)

  await openAppearanceSettings()
  await expect.poll(readZoomPercent, { message: 'UI scale after opening Settings → Appearance' }).toBe(SCALE)
})

test('Cmd/Ctrl+N preserves a non-default UI scale', async () => {
  const page = fixture!.page

  await openSettings()
  await setZoomPercent(SCALE)

  await page.evaluate(() => {
    ;(document.activeElement as HTMLElement | null)?.blur()
  })
  await Promise.all([
    page.waitForURL(url => url.hash === '#/'),
    page.keyboard.press(process.platform === 'darwin' ? 'Meta+N' : 'Control+N')
  ])
  await expect(page.getByRole('button', { name: /close settings|关闭设置/i })).toHaveCount(0)

  await expect.poll(readZoomPercent).toBe(SCALE)
})
