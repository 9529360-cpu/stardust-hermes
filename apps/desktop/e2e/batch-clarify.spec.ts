/**
 * E2E batch clarify test — the multi-question clarify card must mount ONCE.
 *
 * Regression coverage for the duplicated-card bug: `tool.start` carries the
 * model's tool_call_id while `clarify.request` carries a gateway-generated
 * request_id. A batch payload has no top-level `question`, so the two rows
 * only merge when the correlation key comes from the question list
 * (`batchClarifyMatchValue` in lib/chat-messages/tool-parts.ts). Before that
 * fix this exact flow rendered two identical interactive cards.
 *
 * The flow runs the real chain: composer → gateway → agent → clarify tool →
 * clarify.request event → renderer, against the mock inference server.
 */

import { expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { BATCH_CLARIFY_QUESTIONS, BATCH_CLARIFY_TRIGGER } from '../../../tests-js/scripts/mock-server'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture!, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test.describe('batch clarify card', () => {
  test('renders exactly one card and completes via per-question locks', async () => {
    const page = fixture!.page
    const composer = page.locator('[contenteditable="true"]').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })

    await composer.click()
    await composer.type(BATCH_CLARIFY_TRIGGER, { delay: 20 })
    await page.keyboard.press('Enter')

    // Inactive session tabs stay mounted under data-pane-hidden. Bind the
    // live clarify form to the currently visible conversation surface so a
    // warm route switch cannot hand later clicks to a keep-alive copy.
    const activeSurface = () =>
      page.locator('[data-composer-target]:not([data-pane-hidden] [data-composer-target])').last()
    const globalBatchCards = page.locator('form[data-clarify-batch]')
    const batchCard = () => activeSurface().locator('form[data-clarify-batch]')
    await batchCard().waitFor({ state: 'visible', timeout: 60_000 })

    // THE regression assertion: one card, not two.
    await expect(globalBatchCards).toHaveCount(1)
    await expect(batchCard()).toHaveAttribute('data-clarify-batch', String(BATCH_CLARIFY_QUESTIONS.length))

    // Both questions render inside the single card.
    for (const entry of BATCH_CLARIFY_QUESTIONS) {
      await expect(batchCard().getByText(entry.question)).toHaveCount(1)
    }

    // Each question text also appears exactly once in the whole transcript —
    // catches a duplicate that mounts outside a form[data-clarify-batch].
    for (const entry of BATCH_CLARIFY_QUESTIONS) {
      await expect(page.getByText(entry.question)).toHaveCount(1)
    }

    // A blocking clarify is a real task state, not "idle". The Workspace must
    // project the same needsInput/attention truth that drives the live card.
    const productNav = page.locator('[data-personal-product-nav]')
    await productNav.getByRole('button', { name: '工作空间' }).click()

    const workspace = page.locator('[data-jarvis-workspace]')
    const overview = page.locator('[data-personal-overview]')

    await expect(workspace).toBeVisible()
    await expect(workspace.getByText('等待你的输入', { exact: true }).first()).toBeVisible()
    await expect(workspace.getByText('Agent 等待你的输入', { exact: true }).first()).toBeVisible()
    await expect(workspace.getByText('Agent 已就绪', { exact: true })).toHaveCount(0)
    await expect(overview).toBeVisible()
    await expect(overview.getByText(/当前任务正在等待你的确认或补充信息/)).toBeVisible()
    await expect(overview.getByRole('button', { name: '继续处理任务', exact: true })).toBeVisible()
    await expect(overview.getByText('系统状态', { exact: true })).toHaveCount(0)

    // Switch to the real new-chat route so there is no selected stored
    // conversation while the blocking clarify remains live in the background.
    // This exercises the app's route -> selection transition without writing
    // session stores from the test.
    await page.evaluate(() => {
      window.location.hash = '#/'
    })
    await page.waitForFunction(() => window.location.hash === '#/', undefined, { timeout: 15_000 })
    await page.locator('[contenteditable="true"]').last().waitFor({ state: 'visible', timeout: 15_000 })

    await productNav.getByRole('button', { name: '工作空间' }).click()
    await expect(workspace).toBeVisible()

    const resumeTask = workspace.getByRole('button', { name: '继续处理当前任务', exact: true }).first()
    await expect(resumeTask).toBeVisible()
    await expect(overview.getByRole('button', { name: '继续处理任务', exact: true })).toBeVisible()

    await resumeTask.click()
    await expect(batchCard()).toBeVisible()

    // Answer both questions: stage picks locally (no server traffic yet).
    const confirmButton = batchCard().locator('button[type="submit"]')
    await expect(confirmButton).toContainText(/Confirm and continue|确认并继续/)
    await expect(confirmButton).toBeDisabled()

    const coffeeChoice = batchCard().getByRole('button', { name: /Coffee/ })
    await coffeeChoice.click()
    await expect(coffeeChoice).toHaveAttribute('aria-pressed', 'true')
    await expect(batchCard()).toContainText(/1\/2/)
    await expect(confirmButton).toBeDisabled()

    const morningChoice = batchCard().getByRole('button', { name: /Morning/ })
    await morningChoice.click()
    await expect(morningChoice).toHaveAttribute('aria-pressed', 'true')
    await expect(batchCard()).toContainText(/2\/2/)
    await expect(confirmButton).toBeEnabled()

    // ONE confirm submits the whole batch.
    await confirmButton.click()

    // The settled card lists both questions with their locked answers.
    const settled = page.locator('[data-clarify-settled]')
    await settled.waitFor({ state: 'visible', timeout: 30_000 })
    await expect(settled.getByText(BATCH_CLARIFY_QUESTIONS[0].question)).toBeVisible()
    await expect(settled.getByText('Coffee', { exact: true })).toBeVisible()
    await expect(settled.getByText(BATCH_CLARIFY_QUESTIONS[1].question)).toBeVisible()
    await expect(settled.getByText('Morning', { exact: true })).toBeVisible()

    // And still no duplicate live card lingering after settle.
    await expect(page.locator('form[data-clarify-batch]')).toHaveCount(0)

    // Once the answer is accepted, attention must clear as well. A stale
    // needsInput projection would leave Workspace claiming the user still owes
    // an answer even though the clarify card has already settled.
    await productNav.getByRole('button', { name: '工作空间' }).click()
    await expect(workspace).toBeVisible()
    await expect(workspace.getByText('Agent 等待你的输入', { exact: true })).toHaveCount(0)
    await expect(overview.getByText(/当前任务正在等待你的确认或补充信息/)).toHaveCount(0)
    await expect(overview.getByRole('button', { name: '继续处理任务', exact: true })).toHaveCount(0)
  })
})
