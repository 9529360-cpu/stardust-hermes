import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})
test.afterAll(async () => {
  await fixture?.cleanup()
})

test('explicit Desktop pairing reaches the actual local backend and disconnects', async ({}, testInfo) => {
  const page = fixture!.page
  const composer = page.locator('[contenteditable="true"]').first()
  await composer.fill('Browser pairing isolation test')
  await composer.press('Enter')
  await expect.poll(async () => await page.evaluate(() => location.hash)).not.toBe('#/')
  await page.locator('[data-personal-product-nav]').getByRole('button', { name: '工具', exact: true }).click()
  await page.getByText('浏览器自动化', { exact: true }).first().click()
  const connect = page.getByRole('button', { name: '连接 Chrome', exact: true })
  await expect(connect).toBeEnabled({ timeout: 15_000 })
  await page.screenshot({ path: testInfo.outputPath('browser-host-inactive.png') })
  await connect.click()
  await expect(page.getByRole('button', { name: '断开连接', exact: true })).toBeEnabled({ timeout: 25_000 })
  await page.screenshot({ path: testInfo.outputPath('browser-host-connected.png') })
  await page.getByRole('button', { name: '断开连接', exact: true }).click()
  await expect(connect).toBeEnabled({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: '断开连接', exact: true })).toBeDisabled()
})
