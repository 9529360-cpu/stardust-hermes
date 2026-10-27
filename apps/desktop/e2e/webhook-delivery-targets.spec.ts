import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    extraDisplayConfig: '  language: zh',
    extraConfig: 'platforms:\n  webhook:\n    enabled: true'
  })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

// eslint-disable-next-line no-empty-pattern
test('Webhook delivery menu lists retained targets in the native Desktop app', async ({}, testInfo) => {
  const page = fixture!.page

  await page.evaluate(() => {
    window.location.hash = '#/webhooks'
  })
  await expect(page.getByRole('button', { name: '新建订阅', exact: true })).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: '新建订阅', exact: true }).click()
  await page.getByRole('combobox', { name: '投递到' }).click()

  for (const name of ['Telegram', 'WhatsApp', 'WhatsApp Business（云 API）', '微信', 'GitHub 评论']) {
    await expect(page.getByRole('option', { name, exact: true })).toBeVisible()
  }
  for (const retired of ['Discord', 'Slack', '电子邮件']) {
    await expect(page.getByRole('option', { name: retired, exact: true })).toHaveCount(0)
  }

  await page.screenshot({ path: testInfo.outputPath('webhook-delivery-targets.png') })
})
