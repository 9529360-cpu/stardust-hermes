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

test('tasks opens the scheduled-task surface without exposing Cron as the product name', async ({}, testInfo) => {
  const page = fixture!.page
  const productNav = page.locator('[data-personal-product-nav]')
  const tasksButton = productNav.getByRole('button', { name: '任务' })

  await tasksButton.click()
  await expect(tasksButton).toHaveAttribute('aria-current', 'page')
  await expect(page.getByText('定时任务', { exact: true })).toBeVisible()
  await expect(page.getByText('正在加载定时任务…', { exact: true })).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText('暂无排程任务', { exact: true })).toHaveCount(2)
  await expect(page.getByText(/设置一个提示词按计划自动运行/)).toBeVisible()
  await expect(page.getByText('晨间简报', { exact: true })).toBeVisible()
  await expect(page.getByText('每周复盘', { exact: true })).toBeVisible()
  await expect(page.getByText('Morning briefing', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Cron', { exact: true })).toHaveCount(0)

  await page.screenshot({ path: testInfo.outputPath('personal-assistant-tasks.png') })

  await page.getByText('每周复盘', { exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('回顾本周完成事项、未完成事项和接下来要做的事。', { exact: true })).toBeVisible()
  await expect(dialog.getByText('时间', { exact: true })).toBeVisible()
  await expect(dialog.getByText('24 小时本地时间，例如 08:00', { exact: true })).toBeVisible()
  await expect(dialog.getByText('哪一天', { exact: true })).toBeVisible()
  await expect(dialog.getByText('发送到', { exact: true })).toBeVisible()
  await expect(dialog.getByText('机器人聊天（默认）', { exact: true })).toBeVisible()
  const timeInput = dialog.locator('input[type="time"]')
  await expect(timeInput).toBeVisible()
  expect(await timeInput.evaluate(element => getComputedStyle(element).colorScheme)).toContain('dark')
  await expect(dialog.getByText('Weekly review', { exact: true })).toHaveCount(0)
  await expect(dialog.getByText('24h local time, e.g. 08:00', { exact: true })).toHaveCount(0)
  await expect(dialog.getByText('Bot Chat (default)', { exact: true })).toHaveCount(0)
  const dialogBackground = await dialog.evaluate(element => getComputedStyle(element).backgroundColor)
  expect(dialogBackground).toMatch(/(?:21,\s*29,\s*40|0\.0823529\s+0\.113725\s+0\.156863)/)

  await page.screenshot({ path: testInfo.outputPath('personal-assistant-task-blueprint.png') })
})
