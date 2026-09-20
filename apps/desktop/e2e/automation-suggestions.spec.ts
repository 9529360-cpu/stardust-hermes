import * as fs from 'node:fs'
import * as path from 'node:path'

import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)

  const cronDir = path.join(fixture.sandbox.hermesHome, 'cron')
  fs.mkdirSync(cronDir, { recursive: true })
  fs.writeFileSync(
    path.join(cronDir, 'suggestions.json'),
    JSON.stringify(
      {
        suggestions: [
          {
            id: 'e2e-important-mail',
            title: 'Important-mail monitor',
            description: 'Check your inbox periodically and ping you ONLY about mail that actually needs attention.',
            source: 'integration',
            job_spec: {
              prompt: 'private executable prompt that the renderer must not need',
              schedule: '*/30 * * * *',
              name: 'Important-mail monitor',
              deliver: 'origin',
              skills: ['email-inbox-triage']
            },
            dedup_key: 'catalog:important-mail-monitor',
            status: 'pending',
            created_at: '2026-09-19T09:00:00+00:00'
          }
        ]
      },
      null,
      2
    ),
    'utf8'
  )
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('pending automation is localized, consent-gated, and dismissible from Scheduled jobs', async () => {
  const page = fixture!.page
  const tasksButton = page.locator('[data-personal-product-nav]').getByRole('button', { name: '任务' })

  await tasksButton.click()

  await expect(page.getByText('建议的自动化', { exact: true })).toBeVisible()
  const suggestionRow = page.getByText('重要邮件监控', { exact: true }).first()
  await expect(suggestionRow).toBeVisible()
  await expect(page.getByText('Important-mail monitor', { exact: true })).toHaveCount(0)

  await suggestionRow.click()

  await expect(page.getByText('定期检查收件箱，只在确实需要你关注时提醒。', { exact: true })).toBeVisible()
  await expect(page.getByText('*/30 * * * *', { exact: true })).toBeVisible()

  const schedule = page.getByRole('button', { name: '设为定时任务', exact: true })
  await expect(schedule).toBeDisabled()
  await expect(page.getByText(/请先打开一个已保存的对话/)).toBeVisible()

  await page.getByRole('button', { name: '忽略', exact: true }).click()

  await expect(page.getByText('已忽略建议', { exact: true })).toBeVisible()
  await expect(page.getByText('建议的自动化', { exact: true })).toHaveCount(0)
  await expect(page.getByText('重要邮件监控', { exact: true })).toHaveCount(0)
})
