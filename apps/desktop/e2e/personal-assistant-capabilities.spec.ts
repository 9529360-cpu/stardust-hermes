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

test('tools and plugins are direct primary navigation destinations', async ({}, testInfo) => {
  const page = fixture!.page
  const productNav = page.locator('[data-personal-product-nav]')

  await productNav.getByRole('button', { name: '工具', exact: true }).click()
  await expect(productNav.getByRole('button', { name: '工具', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('textbox', { name: '搜索工具集…' })).toBeVisible()
  await expect(page.getByText('正在加载能力…', { exact: true })).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText(/^\d+ 个工具$/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('Hermes (default)', { exact: true })).toHaveCount(0)
  const a2a = page.getByText('A2A 智能体协作', { exact: true }).first()
  await expect(a2a).toBeVisible()
  await a2a.click()
  await expect(page.getByText('浏览器自动化', { exact: true })).toBeVisible()
  await expect(page.getByText('Browser Automation', { exact: true })).toHaveCount(0)
  await expect(page.locator('[data-toolset-technical-details]')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-toolsets.png') })

  // Skills remain available inside the capability surface without occupying a
  // permanent primary-nav row.
  await page.getByText('技能', { exact: true }).click()
  await expect(page.locator('[data-skill-overview]').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('switch').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('它能做什么', { exact: true })).toBeVisible()
  const rawInstructions = page.locator('[data-skill-raw-instructions]').first()
  await expect(rawInstructions).toBeVisible()
  await expect(rawInstructions).not.toHaveAttribute('open', '')
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-skills.png') })

  await productNav.getByRole('button', { name: '插件', exact: true }).click()
  await expect(productNav.getByRole('button', { name: '插件', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('columnheader', { name: '桌面', exact: true })).toBeVisible()
  await expect(page.getByText('任务看板', { exact: true })).toBeVisible()
  await expect(page.getByText('电台', { exact: true })).toBeVisible()
  await expect(page.getByText('插件目录', { exact: true })).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-plugins.png') })
})
