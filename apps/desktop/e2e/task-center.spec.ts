/**
 * Scheduled jobs use the existing automation page; interactive work stays in its conversation.
 */

import { BATCH_CLARIFY_QUESTIONS, BATCH_CLARIFY_TRIGGER } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined

const openScheduledJobs = async (page: MockBackendFixture['page']) => {
  const button = page.getByRole('button', { name: '定时任务', exact: true })

  if (!(await button.isVisible().catch(() => false))) {
    await page.keyboard.press('Control+b')
    await expect(button).toBeVisible({ timeout: 10_000 })
  }

  await button.click()
  await expect(page.locator('[data-cron-page]')).toBeVisible({ timeout: 20_000 })
}

test.afterAll(async () => {
  await fixture?.cleanup()
})

test('scheduled jobs opens directly and legacy task links redirect at wide and narrow sizes', async () => {
  test.setTimeout(180_000)
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: zh' })
  await waitForAppReady(fixture, 120_000)
  const { app, page } = fixture

  for (const width of [1220, 600]) {
    await app.evaluate(
      ({ BrowserWindow }, targetWidth) => BrowserWindow.getAllWindows()[0].setSize(targetWidth, 800),
      width
    )
    await openScheduledJobs(page)
    const scheduledJobs = page.locator('[data-cron-page]')
    await expect(page.locator('[data-task-center]')).toHaveCount(0)
    await expect(scheduledJobs.getByRole('heading', { name: '定时任务', exact: true })).toBeVisible()
    await expect(scheduledJobs.getByRole('button', { name: '新建定时任务', exact: true })).toBeVisible({
      timeout: 20_000
    })

    await page.evaluate(() => {
      window.location.hash = '#/tasks'
    })
    await expect.poll(async () => page.evaluate(() => location.hash)).toBe('#/cron')
    await expect(scheduledJobs).toBeVisible()

    const overflow = await scheduledJobs.evaluate(element => element.scrollWidth > element.clientWidth + 1)
    expect(overflow).toBe(false)
  }
})

test('a pending question stays in its conversation and can be answered there', async () => {
  test.setTimeout(120_000)

  if (!fixture) {
    throw new Error('Native fixture must be ready')
  }

  const { page } = fixture
  await page.locator('[data-personal-product-nav]').getByRole('button', { name: '新建对话', exact: true }).click()
  const composer = page.locator('[contenteditable="true"]').first()
  await expect(composer).toBeVisible()
  await composer.fill(BATCH_CLARIFY_TRIGGER)
  await composer.press('Enter')

  const form = page.locator('form[data-clarify-batch]').first()
  await expect(form).toBeVisible({ timeout: 60_000 })
  await expect(form.getByText(BATCH_CLARIFY_QUESTIONS[0].question)).toBeVisible()
  await form.getByRole('button', { name: /Coffee/ }).click()
  await form.getByRole('button', { name: /Morning/ }).click()
  await form.locator('button[type="submit"]').click()
  await expect(page.locator('[data-clarify-settled]')).toBeVisible({ timeout: 30_000 })
  await expect(form).toHaveCount(0)
})
