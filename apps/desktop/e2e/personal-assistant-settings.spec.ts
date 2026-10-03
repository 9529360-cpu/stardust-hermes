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

test('settings offers one model services page without reviving provider account pages', async ({}, testInfo) => {
  const page = fixture!.page

  // Settings left the primary nav; the titlebar gear is its visible door.
  await page.getByRole('button', { name: '打开设置' }).click()
  await expect(page.getByRole('button', { name: '关闭设置' })).toBeVisible()

  const settingsNav = page.getByRole('complementary')
  const modelServices = settingsNav.getByRole('button', { name: '模型服务' })
  await expect(modelServices).toBeVisible()
  await expect(settingsNav.getByRole('button', { name: '网关' })).toBeVisible()
  await expect(settingsNav.getByText('浏览器', { exact: true })).toBeVisible()
  await expect(settingsNav.getByText('Browser', { exact: true })).toHaveCount(0)
  await expect(settingsNav.getByRole('button', { name: '关于' })).toBeVisible()
  // The retired provider account surfaces stay gone.
  await expect(settingsNav.getByText('账单', { exact: true })).toHaveCount(0)
  await expect(settingsNav.getByRole('button', { name: '网页登录' })).toHaveCount(0)
  await expect(settingsNav.getByRole('button', { name: 'API 密钥' })).toHaveCount(0)
  await expect(page.getByText(/Nous/i)).toHaveCount(0)

  await modelServices.click()
  await expect(modelServices).toHaveClass(/bg-\(--ui-bg-tertiary\)/)
  await expect(page.getByText('自定义服务', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '添加模型服务' })).toBeVisible()
  await expect(page.getByText('服务地址', { exact: true })).toHaveCount(0)

  // Inventory is the default state. Add explicitly enters a create-only editor,
  // and cancelling returns to the inventory instead of leaving an existing
  // service loaded into a reusable form.
  await page.getByRole('button', { name: '添加模型服务' }).click()
  await expect(page.getByText('服务地址', { exact: true })).toBeVisible()
  await expect(page.getByText('默认模型', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('checkbox', { name: '设为新对话默认服务' })).not.toBeChecked()
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByText('服务地址', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/提供方登录|网页登录/)).toHaveCount(0)

  const overlayTheme = await page.locator('[data-overlay-surface]').evaluate(element =>
    getComputedStyle(element).getPropertyValue('--ui-chat-surface-background').trim()
  )
  expect(overlayTheme).not.toBe('')

  await page.screenshot({ path: testInfo.outputPath('personal-assistant-settings.png') })

  const aboutButton = settingsNav.getByRole('button', { name: '关于' })
  await aboutButton.click()
  await expect(aboutButton).toHaveClass(/bg-\(--ui-bg-tertiary\)/)
  await expect(modelServices).not.toHaveClass(/bg-\(--ui-bg-tertiary\)/)

  // A saved legacy provider-account deep link lands on the same Models page.
  await page.evaluate(() => {
    window.location.hash = '#/settings?tab=providers&pview=accounts'
  })
  await expect(modelServices).toHaveClass(/bg-\(--ui-bg-tertiary\)/)
  await expect(page.getByText('自定义服务', { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-settings-legacy-link.png') })
})
