import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

// Settings → Safety shows the approval activity; Settings → Memory & Context shows remembered notes.
// Both read the real backend (`approval.audit`, `approval.grants.list`, `memory.*`), so this spec
// proves the RPC wiring end to end, not only the component logic covered by vitest.

let fixture: MockBackendFixture | null = null

async function openSettingsTab(tab: string): Promise<void> {
  const page = fixture!.page
  const target = `#/settings?tab=${tab}`

  await page.evaluate(hash => {
    window.location.hash = hash
  }, target)
  await page.waitForFunction(hash => window.location.hash === hash, target)
}

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

// eslint-disable-next-line no-empty-pattern
test('safety shows the approval activity empty states from the real backend', async ({}, testInfo) => {
  const page = fixture!.page

  await openSettingsTab('config:safety')

  await expect(page.getByText('我的权限', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('还没有长期权限。需要你批准的事，我都会先问你。')).toBeVisible()
  await expect(page.getByText('还没有需要你确认的操作。')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('safety-approval-activity.png') })
})

// eslint-disable-next-line no-empty-pattern
test('memory notes are remembered and forgotten through the real backend', async ({}, testInfo) => {
  const page = fixture!.page

  await openSettingsTab('config:memory')

  const note = `E2E 偏好公制单位 ${Date.now()}`

  await page.getByPlaceholder('例如：我喜欢简短的回答').fill(note)
  await page.getByRole('button', { name: '记住', exact: true }).click()
  await expect(page.getByText(note, { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: testInfo.outputPath('memory-notes-remembered.png') })

  await page.getByRole('button', { name: '忘记', exact: true }).first().click()
  await page.getByRole('dialog').getByRole('button', { name: '忘记', exact: true }).click()
  await expect(page.getByText(note, { exact: true })).toHaveCount(0, { timeout: 30_000 })
  await page.screenshot({ path: testInfo.outputPath('memory-notes-forgotten.png') })
})
