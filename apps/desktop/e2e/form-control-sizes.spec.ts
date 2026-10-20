import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

const DESKTOP_WINDOW = { height: 800, width: 1220 }
const NARROW_WINDOW = { height: 720, width: 900 }

async function resizeWindow(target: MockBackendFixture, size: { height: number; width: number }) {
  await target.app.evaluate(({ BrowserWindow }, bounds) => {
    BrowserWindow.getAllWindows()[0].setSize(bounds.width, bounds.height)
  }, size)
  await target.page.waitForTimeout(600)
}

test.beforeAll(async () => {
  test.setTimeout(240_000)
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: en' })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('the Cron editor keeps its shared 36px form fields at desktop and narrow window sizes', async () => {
  const page = fixture!.page

  await resizeWindow(fixture!, DESKTOP_WINDOW)
  await page.getByRole('button', { name: /^(任务|Tasks)$/ }).click()
  const taskCenter = page.locator('[data-task-center]')
  await expect(taskCenter).toBeVisible()
  await taskCenter.getByRole('button', { name: /^(自动化|Automation)$/ }).click()
  await expect(page.locator('[data-cron-page]')).toBeVisible()

  const newCron = page.getByRole('button', { name: 'New cron', exact: true })
  await expect(newCron).toBeVisible({ timeout: 20_000 })
  await newCron.click()

  const frequency = page.locator('#cron-frequency')
  await expect(frequency).toBeVisible()

  const desktopGeometry = await frequency.evaluate(element => {
    const style = getComputedStyle(element)

    return {
      borderRadius: style.borderTopLeftRadius,
      height: style.height
    }
  })

  expect(desktopGeometry).toEqual({ borderRadius: '6px', height: '36px' })
  await page.screenshot({ path: test.info().outputPath('cron-medium-control-1220x800.png') })

  await resizeWindow(fixture!, NARROW_WINDOW)
  await expect(frequency).toBeVisible()

  const narrowGeometry = await frequency.evaluate(element => {
    const style = getComputedStyle(element)

    return {
      borderRadius: style.borderTopLeftRadius,
      height: style.height
    }
  })

  expect(narrowGeometry).toEqual({ borderRadius: '6px', height: '36px' })
  await page.screenshot({ path: test.info().outputPath('cron-medium-control-900x720.png') })
  await page
    .getByRole('button', { name: /Cancel/ })
    .last()
    .click()
})

test('Bot Routines keeps its 32px schedule fields separate from the main Cron form', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page

  await resizeWindow(fixture!, DESKTOP_WINDOW)
  await page
    .getByRole('button', { name: /Agent space/ })
    .first()
    .click()

  const agentSpace = page.locator('[data-slot-page="agents-home"]')
  await expect(agentSpace).toBeVisible({ timeout: 20_000 })
  await agentSpace
    .locator('[data-agent-card]')
    .first()
    .getByRole('button', { name: /Start a chat/ })
    .click()
  await expect(page.locator('[data-slot="bot_chat_empty"]')).toBeVisible({ timeout: 30_000 })

  const routinesRail = page.locator('[data-tree-tab$=":routines"][data-vertical]')
  await expect(routinesRail).toBeVisible()
  await routinesRail.click()
  await expect(page.locator('[data-tree-tab$=":routines"]:not([data-vertical])')).toBeVisible()
  await page
    .getByRole('button', { name: /New cron/ })
    .first()
    .click()

  const dialog = page.getByRole('dialog').last()
  await expect(dialog).toBeVisible()
  const frequency = dialog.locator('[data-slot="select-trigger"]').first()
  const repeatCount = dialog.getByPlaceholder('∞')
  await expect(frequency).toBeVisible()
  await expect(repeatCount).toBeVisible()

  const scheduleGeometry = await frequency.evaluate(element => {
    const style = getComputedStyle(element)

    return {
      borderRadius: style.borderTopLeftRadius,
      height: style.height
    }
  })

  const compactGeometry = await repeatCount.evaluate(element => {
    const style = getComputedStyle(element)

    return {
      borderRadius: style.borderTopLeftRadius,
      height: style.height
    }
  })

  expect(scheduleGeometry).toEqual({ borderRadius: '6px', height: '32px' })
  expect(compactGeometry).toEqual({ borderRadius: '2.5px', height: '28px' })
  await page.screenshot({ path: test.info().outputPath('bot-routine-control-1220x800.png') })

  await frequency.click()
  await page.getByRole('option', { name: /Once, in/ }).click()
  const onceCount = dialog.getByPlaceholder('30')
  await expect(onceCount).toBeVisible()

  const onceGeometry = await onceCount.evaluate(element => {
    const style = getComputedStyle(element)

    return {
      borderRadius: style.borderTopLeftRadius,
      height: style.height
    }
  })

  expect(onceGeometry).toEqual({ borderRadius: '6px', height: '32px' })
  await onceCount.fill('45')
  await expect(onceCount).toHaveValue('45')

  await resizeWindow(fixture!, NARROW_WINDOW)
  await expect(dialog).toBeVisible()
  await expect(onceCount).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('bot-routine-control-900x720.png') })
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dialog).toBeHidden()
})
