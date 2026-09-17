import { buildAppEnv, createSandbox, launchDesktop, setupNoProvider, waitForOnboarding } from './fixtures'
import { expect, test } from './test'

test.describe('connector onboarding boundaries', () => {
  test('requires provider setup before the guided connector flow can start', async () => {
    const fixture = await setupNoProvider({ HERMES_GUIDED_ONBOARDING: '1' })

    try {
      await waitForOnboarding(fixture.page, 90_000)
      await expect(fixture.page.locator('[data-desktop-onboarding]')).toBeVisible()
      await expect(fixture.page.locator('[data-connector-offer]')).toHaveCount(0)
    } finally {
      await fixture.cleanup()
    }
  })

  test('keeps the default surface free of connector authorization controls', async () => {
    const sandbox = createSandbox('connectors-off')
    const { app, page } = await launchDesktop(buildAppEnv(sandbox))

    try {
      await expect(page.locator('[data-connector-offer]')).toHaveCount(0)
    } finally {
      await app.close().catch(() => undefined)
      sandbox.cleanup()
    }
  })
})
