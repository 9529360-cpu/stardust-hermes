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

test('messaging presents known channels as assistant product surfaces in Chinese', async ({}, testInfo) => {
  const page = fixture!.page

  await page.evaluate(() => {
    window.location.hash = '#/messaging'
  })
  await expect(page.getByRole('textbox', { name: '搜索消息平台…' })).toBeVisible()
  await expect(page.getByText('正在加载消息平台…', { exact: true })).toHaveCount(0, { timeout: 15_000 })

  await page.getByRole('button', { name: 'Telegram', exact: true }).click()
  await expect(page.getByText('通过 Telegram 机器人随时与助理对话，并接收提醒和任务结果。', { exact: true })).toBeVisible()
  await expect(page.getByText('扫描二维码并在 Telegram 中确认。助理会自动创建机器人并识别你的 Telegram 用户 ID。', { exact: true })).toBeVisible()
  await expect(page.getByText(/Hermes creates the bot/i)).toHaveCount(0)
  await expect(page.getByText(/此 Hermes 安装中/)).toHaveCount(0)

  await page.screenshot({ path: testInfo.outputPath('personal-assistant-messaging.png') })
})
