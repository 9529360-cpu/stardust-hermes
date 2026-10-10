/**
 * Projects expanded with no projects says so, on the line above the new-project control,
 * at the full and the narrow width.
 *
 * SHELL_STATES_SCREENSHOT_DIR=<dir> saves captures for design review; never part of the
 * assertions.
 */
import fs from 'node:fs'
import path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const NARROW_WIDTH = 560
const NARROW_HEIGHT = 820

let fixture: MockBackendFixture | null = null

async function capture(page: MockBackendFixture['page'], name: string): Promise<void> {
  const dir = process.env.SHELL_STATES_SCREENSHOT_DIR

  if (!dir) {
    return
  }

  fs.mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: path.join(dir, `${name}.png`) })
}

async function expandProjects(page: MockBackendFixture['page'], narrow: boolean): Promise<void> {
  const projects = page.getByRole('button', { name: /^(项目|Project)$/ }).first()

  if (narrow && !(await projects.isVisible().catch(() => false))) {
    await page.keyboard.press('Control+b')
    await expect(projects).toBeVisible({ timeout: 10_000 })
  }

  await projects.click()
  await expect(projects).toHaveAttribute('aria-expanded', 'true')
}

async function expectEmptyLineAboveCreate(page: MockBackendFixture['page'], widthName: string): Promise<void> {
  const empty = page.getByText(/^(暂无项目|No projects yet)$/)
  const create = page.getByRole('button', { name: /新建项目|New project/ })

  await expect(empty).toBeVisible({ timeout: 20_000 })
  await expect(create).toBeVisible()

  const emptyBox = await empty.boundingBox()
  const createBox = await create.boundingBox()
  expect(emptyBox).not.toBeNull()
  expect(createBox).not.toBeNull()
  expect(emptyBox?.y ?? 0).toBeLessThan(createBox?.y ?? 0)

  await capture(page, `${widthName}-projects-expanded`)
}

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('at 1220x800 an expanded Projects list with no projects says so above the new-project control', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await expandProjects(page, false)
  await expectEmptyLineAboveCreate(page, '1220x800')
})

test('at 560x820 the empty Projects list says so above the new-project control', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await fixture!.app.evaluate(
    ({ BrowserWindow }, size) => {
      BrowserWindow.getAllWindows()[0].setSize(size.width, size.height)
    },
    { width: NARROW_WIDTH, height: NARROW_HEIGHT }
  )
  await page.waitForTimeout(600)

  await expandProjects(page, true)
  await expectEmptyLineAboveCreate(page, '560x820')
})
