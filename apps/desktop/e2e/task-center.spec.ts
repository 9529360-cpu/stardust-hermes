import fs from 'node:fs'
import path from 'node:path'

import {
  BATCH_CLARIFY_QUESTIONS,
  BATCH_CLARIFY_TRIGGER,
  createBackgroundReleaseHandle
} from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
const historyRelease = createBackgroundReleaseHandle()
const python = (process.env.STARDUST_DESKTOP_PYTHON ?? 'python').replaceAll('\\', '/')
const releasePath = historyRelease.path.replaceAll('\\', '/')
const historyCommand = `"${python}" -c "import pathlib,time; p=pathlib.Path('${releasePath}'); [time.sleep(0.1) for _ in range(600) if not p.exists()]; print('TASK_HISTORY_DONE')"`

test.afterAll(async () => {
  historyRelease.release()
  await fixture?.cleanup()
  historyRelease.cleanup()
})

test('task navigation and activity/automation tabs work in native Electron at wide and narrow sizes', async () => {
  test.setTimeout(240_000)
  fixture = await setupMockBackend({
    extraDisplayConfig: '  language: zh',
    extraConfig: 'auxiliary:\n  title_generation:\n    enabled: false',
    mockServer: {
      autoShowScripts: {
        taskhistory: [
          {
            text: 'Starting task history check.',
            toolCalls: [{ name: 'terminal', args: { command: historyCommand, background: true } }]
          },
          { text: 'TASK_HISTORY_STARTED' }
        ]
      }
    },
    seedSandbox(sandbox) {
      // Prevent a developer shell's legacy home/auth overrides reaching the test backend.
      process.env.HERMES_HOME = sandbox.hermesHome
      process.env.HERMES_SHARED_AUTH_DIR = path.join(sandbox.hermesHome, 'shared')
    }
  })
  await waitForAppReady(fixture, 120_000)
  const { app, page } = fixture
  await page.getByRole('button', { name: '任务', exact: true }).click()
  const center = page.locator('[data-task-center]')
  await expect(center.getByRole('heading', { name: '任务中心' })).toBeVisible()
  await expect(center.getByText('暂时没有活动任务')).toBeVisible()

  for (const width of [1220, 600]) {
    await app.evaluate(({ BrowserWindow }, width) => BrowserWindow.getAllWindows()[0].setSize(width, 800), width)
    await center.getByRole('button', { name: '自动化', exact: true }).click()
    await expect(page.locator('[data-task-automation]')).toBeVisible()
    await center.getByRole('button', { name: '活动', exact: true }).click()
    await expect(center.getByText('暂时没有活动任务')).toBeVisible()
    const overflow = await center.evaluate(element => element.scrollWidth > element.clientWidth + 1)
    expect(overflow).toBe(false)
    const screenshots = process.env.TASK_CENTER_SCREENSHOT_DIR

    if (screenshots) {
      fs.mkdirSync(screenshots, { recursive: true })
      await page.screenshot({ path: path.join(screenshots, `tasks-${width}.png`) })
    }
  }

  const sandboxFiles = fs.readdirSync(fixture.sandbox.hermesHome)
  expect(sandboxFiles).not.toContain('tasks.db')
})

test('a real pending clarify appears as a task and returns to its owning conversation', async () => {
  test.setTimeout(180_000)

  if (!fixture) {
    throw new Error('Native fixture must be ready')
  }

  const { app, page } = fixture
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1220, 800))
  await page.locator('[data-personal-product-nav]').getByRole('button', { name: '新建对话', exact: true }).click()
  const composer = page.locator('[contenteditable="true"]').first()
  await composer.waitFor({ state: 'visible', timeout: 15_000 })
  await composer.fill(BATCH_CLARIFY_TRIGGER)
  await composer.press('Enter')
  const form = page.locator('form[data-clarify-batch]').first()
  await expect(form).toBeVisible({ timeout: 60_000 })
  await page.getByRole('button', { name: '任务', exact: true }).click()
  const attention = page.locator('[data-task-group="attention"]')
  await expect(attention).toBeVisible()
  const task = attention.getByRole('button').filter({ hasText: BATCH_CLARIFY_QUESTIONS[0].question }).first()
  await expect(task).toBeVisible()
  await task.click()
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('button', { name: /Coffee/ }).click()
  await form.getByRole('button', { name: /Morning/ }).click()
  await form.locator('button[type="submit"]').click()
  await expect(page.locator('[data-clarify-settled]')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: '任务', exact: true }).click()
  await expect(page.locator('[data-task-group="attention"]')).toHaveCount(0)
})

test('background completion is retained after leaving Tasks, source dismissal, and route remount', async () => {
  test.setTimeout(180_000)

  if (!fixture) {
    throw new Error('Native fixture must be ready')
  }

  const { page } = fixture

  const newChat = () =>
    page.locator('[data-personal-product-nav]').getByRole('button', { name: '新建对话', exact: true }).click()

  await newChat()
  const composer = page.locator('[contenteditable="true"]').first()
  await composer.fill('E2E_AUTO_SHOW(taskhistory)')
  await composer.press('Enter')
  // The interim tool transcript and final reply can both contain this marker.
  await expect(page.locator('[data-slot="aui_assistant-message-root"]').getByText('TASK_HISTORY_STARTED', { exact: true }).last()).toBeVisible({ timeout: 60_000 })
  // Keep Tasks unmounted while the real background command exits and its 4s source row clears.
  // The 30s completion linger must also elapse before the row belongs in Recent.
  historyRelease.release()
  await page.waitForTimeout(36_000)
  await page.getByRole('button', { name: '任务', exact: true }).click()
  const recent = page.locator('[data-task-group="recent"]')
  await expect(recent.getByRole('button').filter({ hasText: historyCommand.slice(0, 200) })).toHaveCount(1, { timeout: 15_000 })
  await newChat()
  await page.getByRole('button', { name: '任务', exact: true }).click()
  await expect(recent.getByRole('button').filter({ hasText: historyCommand.slice(0, 200) })).toHaveCount(1)
  const screenshots = process.env.TASK_CENTER_SCREENSHOT_DIR

  if (screenshots) {
    await page.screenshot({ path: path.join(screenshots, 'task-history-after-remount.png') })
  }
})
