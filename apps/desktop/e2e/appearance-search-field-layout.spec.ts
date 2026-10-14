import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

interface WindowSize {
  height: number
  name: string
  width: number
}

const WINDOW_SIZES: WindowSize[] = [
  { height: 800, name: '1220x800', width: 1220 },
  { height: 820, name: '560x820', width: 560 }
]

async function setWindowSize(fixture: MockBackendFixture, size: WindowSize): Promise<void> {
  await fixture.app.evaluate(({ BrowserWindow }, target) => {
    BrowserWindow.getAllWindows()[0].setSize(target.width, target.height)
  }, size)

  await expect
    .poll(
      () =>
        fixture.page.evaluate(
          ({ height, width }) => Math.abs(window.innerWidth - width) <= 2 && Math.abs(window.innerHeight - height) <= 2,
          size
        ),
      { timeout: 10_000 }
    )
    .toBe(true)
}

// eslint-disable-next-line no-empty-pattern -- Playwright requires fixture destructuring to expose testInfo.
test('Appearance SearchField stays within the Settings window at wide and narrow sizes', async ({}, testInfo) => {
  testInfo.setTimeout(240_000)
  const fixture = await setupMockBackend({ extraDisplayConfig: '  language: en' })

  try {
    await waitForAppReady(fixture, 120_000)

    const page = fixture.page

    await setWindowSize(fixture, WINDOW_SIZES[0])
    await page.getByRole('button', { name: 'Open settings', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Close settings', exact: true })).toBeVisible()

    const settingsNav = page.getByRole('complementary')
    await settingsNav.getByRole('button', { name: 'Appearance', exact: true }).click()

    const search = page.getByRole('textbox', { name: /Search your themes/ })

    for (const size of WINDOW_SIZES) {
      await setWindowSize(fixture, size)
      await expect(search).toBeVisible()

      const bounds = await search.evaluate(input => {
        const container = input.parentElement

        if (!container) {
          throw new Error('SearchField input has no container')
        }

        const rect = container.getBoundingClientRect()

        return {
          clientWidth: container.clientWidth,
          left: rect.left,
          right: rect.right,
          scrollWidth: container.scrollWidth,
          viewportWidth: window.innerWidth
        }
      })

      expect(bounds.left).toBeGreaterThanOrEqual(0)
      expect(bounds.right).toBeLessThanOrEqual(bounds.viewportWidth + 1)
      expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth + 1)

      await page.screenshot({ path: testInfo.outputPath(`appearance-search-field-${size.name}.png`) })
    }
  } finally {
    await fixture.cleanup()
  }
})
