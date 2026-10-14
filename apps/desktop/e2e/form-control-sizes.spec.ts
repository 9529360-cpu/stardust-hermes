import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  test.setTimeout(240_000)
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: en' })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('cron form controls render the shared medium field size', async ({}, testInfo) => {
  const page = fixture!.page

  await page.evaluate(() => {
    window.location.hash = '#/cron'
  })
  await page.getByRole('button', { name: 'New cron', exact: true }).click()

  const frequency = page.locator('#cron-frequency')
  await expect(frequency).toBeVisible()
  const geometry = await frequency.evaluate(element => {
    const style = getComputedStyle(element)

    return {
      borderRadius: style.borderTopLeftRadius,
      height: style.height
    }
  })

  expect(geometry).toEqual({ borderRadius: '6px', height: '36px' })
  await page.screenshot({ path: testInfo.outputPath('cron-medium-control.png') })
})
