import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('settings exposes provider configuration without reviving the legacy app account surface', async ({}, testInfo) => {
  const page = fixture!.page
  const productNav = page.locator('[data-personal-product-nav]')

  await productNav.getByRole('button', { name: '设置' }).click()
  await expect(page.getByRole('button', { name: '关闭设置' })).toBeVisible()

  const settingsNav = page.getByRole('complementary')
  await expect(settingsNav.getByRole('button', { name: /提供方/ })).toBeVisible()
  await expect(settingsNav.getByRole('button', { name: '网关' })).toBeVisible()
  await expect(settingsNav.getByText('浏览器', { exact: true })).toBeVisible()
  await expect(settingsNav.getByText('Browser', { exact: true })).toHaveCount(0)
  await expect(settingsNav.getByRole('button', { name: '关于' })).toBeVisible()
  await expect(settingsNav.getByText('账单', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/Nous/i)).toHaveCount(0)
  await settingsNav.getByRole('button', { name: /提供方/ }).click()
  await expect(settingsNav.getByRole('button', { name: '账号' })).toBeVisible()
  await expect(settingsNav.getByRole('button', { name: 'API 密钥' })).toBeVisible()
  await expect(settingsNav.getByRole('button', { name: '自定义端点' })).toBeVisible()
  await expect(settingsNav.getByText('账单', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/Nous/i)).toHaveCount(0)
  await expect(page.getByText(/Stardust 会在应用中为你完成浏览器登录/)).toBeVisible()
  await expect(page.getByText(/Hermes 会在应用中为你完成浏览器登录/)).toHaveCount(0)

  const overlayTheme = await page.locator('[data-overlay-surface]').evaluate(element =>
    getComputedStyle(element).getPropertyValue('--ui-chat-surface-background').trim()
  )
  expect(['#081021e6', 'rgba(8, 16, 33, 0.9)']).toContain(overlayTheme)

  await page.screenshot({ path: testInfo.outputPath('personal-assistant-settings.png') })

  const aboutButton = settingsNav.getByRole('button', { name: '关于' })
  const providersButton = settingsNav.getByRole('button', { name: /提供方/ })
  await aboutButton.click()
  await expect(aboutButton).toHaveClass(/bg-\(--ui-bg-tertiary\)/)
  await expect(providersButton).not.toHaveClass(/bg-\(--ui-bg-tertiary\)/)
  await expect(page.getByRole('heading', { name: 'Stardust Desktop' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Hermes Desktop' })).toHaveCount(0)
  await expect(page.getByText('更新', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-about.png') })
})
