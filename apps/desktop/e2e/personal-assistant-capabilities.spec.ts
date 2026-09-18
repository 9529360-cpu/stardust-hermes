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

test('capabilities stays localized across skills, toolsets, and plugins', async ({}, testInfo) => {
  const page = fixture!.page

  await page.locator('[data-personal-product-nav]').getByRole('button', { name: '工具', exact: true }).click()
  await expect(page.getByRole('button', { name: /技能/ }).first()).toBeVisible()
  await expect(page.locator('[data-skill-overview]').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('switch').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('正在加载能力…', { exact: true })).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText('Hermes (default)', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '浏览完整技能中心' })).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect(page.getByText('它能做什么', { exact: true })).toBeVisible()
  await expect(page.getByText('效率', { exact: true }).first()).toBeVisible()
  const rawInstructions = page.locator('[data-skill-raw-instructions]').first()
  const skillMetadata = page.locator('[data-skill-technical-metadata]').first()
  await expect(rawInstructions).toBeVisible()
  await expect(skillMetadata).toBeVisible()
  await expect(rawInstructions).not.toHaveAttribute('open', '')
  await expect(skillMetadata).not.toHaveAttribute('open', '')

  // Advanced users can still inspect the exact source without making it the
  // default product surface.
  await page.getByText('原始说明', { exact: true }).click()
  await expect(rawInstructions).toHaveAttribute('open', '')
  await expect(rawInstructions.locator('pre')).toBeVisible()
  await page.getByText('原始说明', { exact: true }).click()
  await expect(rawInstructions).not.toHaveAttribute('open', '')

  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-skills.png') })
  await page.getByText('工具集', { exact: true }).click()
  await expect(page.getByRole('textbox', { name: '搜索工具集…' })).toBeVisible()
  await expect(page.getByText('正在加载能力…', { exact: true })).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText(/^\d+ 个工具$/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText(/^\d+ tools$/)).toHaveCount(0)
  await expect(page.getByText('Hermes (default)', { exact: true })).toHaveCount(0)
  const a2a = page.getByText('A2A 智能体协作', { exact: true }).first()
  await expect(a2a).toBeVisible()
  await a2a.click()
  await expect(page.getByText('浏览器自动化', { exact: true })).toBeVisible()
  await expect(page.getByText('Browser Automation', { exact: true })).toHaveCount(0)
  const technicalDetails = page.locator('[data-toolset-technical-details]')
  await expect(technicalDetails).toBeVisible()
  await expect(technicalDetails).not.toHaveAttribute('open', '')
  await expect(page.getByText('技术说明', { exact: true })).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-toolsets.png') })

  await page.getByText('插件', { exact: true }).click()
  await expect(page.getByRole('columnheader', { name: '桌面', exact: true })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '默认配置 中的智能体', exact: true })).toBeVisible()
  await expect(page.getByText('智能体', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('任务看板', { exact: true })).toBeVisible()
  await expect(page.getByText('电台', { exact: true })).toBeVisible()
  await expect(page.getByText('智能体名册，为每个智能体提供独立对话、定时任务、群聊和智能体间消息。', { exact: true })).toBeVisible()
  await expect(page.getByText('多智能体任务看板，包含看板页面、侧栏入口和状态栏中的实时任务动态。', { exact: true })).toBeVisible()
  await expect(page.getByText('在线电台，支持固定常用电台、搜索和随音频变化的波形显示。', { exact: true })).toBeVisible()
  await expect(page.getByText('Hermes（默认）', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Hermes (default)', { exact: true })).toHaveCount(0)
  await expect(page.getByText('插件目录', { exact: true })).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('personal-assistant-capabilities-plugins.png') })
})
