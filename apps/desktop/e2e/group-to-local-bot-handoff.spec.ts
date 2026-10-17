import { MOCK_REPLY } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

async function openBots(page: MockBackendFixture['page']): Promise<void> {
  const home = page.locator('[data-slot-page="agents-home"]')

  if (!(await home.isVisible())) {
    await page.getByRole('button', { name: /智能体空间|Agent space/ }).first().click()
    await expect(home).toBeVisible()
  }

  if (!(await page.locator('[data-slot-page="agents-manager"]').isVisible())) {
    await page.getByRole('button', { name: 'Manage', exact: true }).click()
  }

  await expect(page.locator('[data-slot="bots-roster"]')).toBeVisible()
}

async function createAgent(page: MockBackendFixture['page'], name: string, title: string): Promise<void> {
  if (!(await page.locator('[data-slot-page="agents-manager"]').isVisible())) {
    await page.getByRole('button', { name: 'Manage', exact: true }).click()
    await expect(page.locator('[data-slot="bots-roster"]')).toBeVisible()
  }

  await page.getByRole('button', { name: 'New bot or group chat' }).click()
  await page.getByRole('menuitem', { name: 'New Bot' }).click()

  const dialog = page.getByRole('dialog', { name: 'New Bot' })
  await dialog.getByPlaceholder('inbox-triage').fill(name)
  await dialog.getByPlaceholder('Inbox Triage').fill(title)
  await dialog.getByRole('button', { name: 'Create Bot' }).click()
  await expect(dialog).toBeHidden({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Back to my partners', exact: true }).click()
  await expect(page.locator('[data-slot-page="agents-home"]')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator(`[data-agent-card="${name}"]`)).toBeVisible({ timeout: 30_000 })
}

test.beforeAll(async () => {
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: en' })
  await waitForAppReady(fixture, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('local bot replaces an open group main workspace', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page

  await openBots(page)
  await createAgent(page, 'programmer', 'Programmer')
  await createAgent(page, 'reviewer', 'Reviewer')
  // Group creation is a Home action; leave Manage before looking for it.
  if (await page.locator('[data-slot-page="agents-manager"]').isVisible()) {
    await page.getByRole('button', { name: 'Back to my partners', exact: true }).click()
    await expect(page.locator('[data-slot-page="agents-home"]')).toBeVisible()
  }

  await page.getByRole('button', { name: 'Create a team' }).click()

  const dialog = page.getByRole('dialog', { name: 'New Group Chat' })

  for (const title of ['Programmer', 'Reviewer']) {
    await dialog.getByText(title, { exact: true }).locator('xpath=ancestor::label').getByRole('checkbox').click()
  }

  await dialog.getByRole('textbox', { name: 'Group name' }).fill('Programmer, Reviewer')
  await dialog.getByRole('button', { name: 'Create Group (2)' }).click()

  const groupTab = page.getByRole('tab', { name: /Programmer, Reviewer Close/ })
  const groupComposer = page.getByRole('textbox', { name: 'Message Programmer, Reviewer' }).filter({ visible: true })
  await expect(groupTab).toBeVisible({ timeout: 20_000 })
  await expect(groupTab).toHaveAttribute('aria-selected', 'true')
  await expect(groupComposer).toBeVisible()

  // Creating the group leaves the Agent space, so the roster row is reached again from it.
  await openBots(page)
  const programmer = page
    .getByRole('button', { name: /^Programmer\b/ })
    .filter({ visible: true })
    .first()
  await programmer.click()

  // Creating a new bot sends its self-introduction kickoff immediately, so
  // the canonical chat is populated rather than showing the empty-state card.
  await expect(page.getByText('Hey, tell me about yourself!', { exact: true }).filter({ visible: true })).toBeVisible({
    timeout: 120_000
  })
  await expect(groupTab).toHaveCount(0)
  await expect(groupComposer).toHaveCount(0)
  await expect(
    page.locator('[data-slot="composer-root"] [contenteditable="true"]').filter({ visible: true }).first()
  ).toBeVisible({ timeout: 15_000 })
})
