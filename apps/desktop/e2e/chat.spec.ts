/**
 * E2E chat tests — send a message and verify a response appears.
 *
 * Requires the full boot chain to complete (hermes serve + mock inference
 * provider). The mock server returns a canned reply, so we verify the
 * response text shows up in the chat transcript.
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import { allowErrorBanners, expect, test } from './test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { BLOCKING_CLARIFY_QUESTION, BLOCKING_CLARIFY_TRIGGER } from '../../../tests-js/scripts/mock-server'
import { expectVisualSnapshot } from './visual-snapshot'

const STOP_STREAM_TRIGGER = 'E2E_CHAT_STOP_STREAM_TRIGGER'
const STOP_STREAM_PARTIAL = 'PARTIAL_CHAT_STOP_TOKEN'
const STOP_STREAM_REMAINDER = 'MUST_NOT_ARRIVE_AFTER_STOP'
const AFTER_STOP_TRIGGER = 'E2E_CHAT_AFTER_STOP_TRIGGER'
const AFTER_STOP_REPLY = 'Chat is still available after stopping the previous reply.'
const RETRY_TRIGGER = 'E2E_CHAT_RETRY_TRIGGER'
const RETRY_REPLY = 'The explicit retry completed successfully.'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    extraConfig: `agent:\n  api_max_retries: 1`,
    mockServer: {
      holdFirstStreamForPrompt: STOP_STREAM_TRIGGER,
      failCompletionsContaining: { prompt: RETRY_TRIGGER, count: 1 },
      replyForPrompt: prompt => {
        if (prompt.includes(STOP_STREAM_TRIGGER)) {
          return `${STOP_STREAM_PARTIAL} ${STOP_STREAM_REMAINDER} and the rest of the held response.`
        }

        if (prompt.includes(AFTER_STOP_TRIGGER)) {
          return AFTER_STOP_REPLY
        }

        if (prompt.includes(RETRY_TRIGGER)) {
          return RETRY_REPLY
        }

        return 'Hello from the mock inference server! The full boot chain is working.'
      }
    }
  })
  await waitForAppReady(fixture!, 120_000)
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test.describe('chat interaction with mock backend', () => {
  test('send a message and receive a response', async () => {
    const page = fixture!.page

    // Find the composer — it's a contenteditable textbox.
    const composer = page.locator('[contenteditable="true"]').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })

    // Click to focus, then type the message character by character.
    // Using `type` instead of `fill` because the composer is a
    // contenteditable div with custom keydown handling that tracks
    // IME composition state — `fill` bypasses the event chain.
    await composer.click()
    await composer.type('Hello, can you hear me?', { delay: 20 })

    // Submit with Enter — the composer's keydown handler intercepts
    // plain Enter (without Shift) and calls submitDraft().
    await page.keyboard.press('Enter')

    // Wait for the user's message to appear in the transcript.
    // The message renders as an assistant-ui message in the chat view.
    await page.waitForFunction(
      () => {
        const body = document.body

        if (!body) {
          return false
        }

        return (body.textContent ?? '').includes('Hello, can you hear me?')
      },
      undefined,
      { timeout: 15_000 }
    )

    // Wait for the mock response to appear. The canned reply is:
    // "Hello from the mock inference server! The full boot chain is working."
    // Give it a generous timeout — the inference request goes through the
    // gateway → hermes serve → mock server → streaming SSE back.
    await page.waitForFunction(
      () => {
        const body = document.body

        if (!body) {
          return false
        }

        const text = body.textContent ?? ''

        return text.includes('mock inference server') || text.includes('boot chain is working')
      },
      undefined,
      { timeout: 60_000 }
    )
  })

  test('screenshot of chat with messages', async () => {
    await expectVisualSnapshot(fixture!.page, { name: 'chat-with-messages', app: fixture!.app })
  })

  test('stops a streamed reply and accepts a follow-up message', async () => {
    const page = fixture!.page
    const viewport = page.locator('[data-slot="aui_thread-viewport"]')
    const composer = page.locator('[contenteditable="true"]').first()
    const primary = page.locator('[data-slot="composer-root"] button[type="submit"]')

    await composer.click()
    await composer.type(STOP_STREAM_TRIGGER, { delay: 10 })
    await page.keyboard.press('Enter')

    // The mock pauses after sending the first SSE chunk. Wait for that chunk
    // in the actual transcript before exercising the Stop control.
    await fixture!.mock.waitForHeldStream()
    await expect(viewport).toContainText(STOP_STREAM_PARTIAL)
    await expect(primary).toHaveAttribute('aria-label', /^(Stop|停止)$/)

    await primary.click()
    await expect(composer).toBeEditable()
    fixture!.mock.releaseHeldStream()

    // Stop must leave the composer usable for a follow-up, and releasing the
    // mock must not deliver the rest of the stale response into the transcript.
    await composer.click()
    await composer.type(AFTER_STOP_TRIGGER, { delay: 10 })
    await page.keyboard.press('Enter')
    await expect(viewport).toContainText(AFTER_STOP_REPLY, { timeout: 60_000 })
    await expect(viewport).not.toContainText(STOP_STREAM_REMAINDER)
    await expect(composer).toBeEditable()
  })

  test('retries a failed provider turn and receives a successful reply', async () => {
    allowErrorBanners()

    const page = fixture!.page
    const viewport = page.locator('[data-slot="aui_thread-viewport"]')
    const composer = page.locator('[contenteditable="true"]').first()
    const retry = page.getByRole('button', { name: /^(Retry|重试)$/ }).last()

    await composer.click()
    await composer.type(RETRY_TRIGGER, { delay: 10 })
    await page.keyboard.press('Enter')

    await expect(retry).toBeVisible({ timeout: 60_000 })
    await expect.poll(() => fixture!.mock.failedCompletionCount()).toBe(1)

    await retry.click()
    await expect(viewport).toContainText(RETRY_REPLY, { timeout: 60_000 })
    await expect(viewport.getByRole('button', { name: /^(Retry|重试)$/ })).toHaveCount(0)
  })

  test('offers stop, steer, and queue actions while busy', async ({}, testInfo) => {
    const page = fixture!.page
    const composer = page.locator('[contenteditable="true"]').first()
    const primary = page.locator('[data-slot="composer-root"] button[type="submit"]')
    const queue = page.locator('[data-slot="composer-root"] button[aria-label="排队消息"]')
    const dictation = page.locator('[data-slot="composer-root"] button[aria-label="语音听写"]')
    const speakReplies = page.locator(
      '[data-slot="composer-root"] button[aria-label="朗读回复"], [data-slot="composer-root"] button[aria-label="停止朗读回复"]'
    )

    await composer.click()
    await composer.type(BLOCKING_CLARIFY_TRIGGER)
    await page.keyboard.press('Enter')
    await page.getByText(BLOCKING_CLARIFY_QUESTION).waitFor({ state: 'visible', timeout: 30_000 })

    await expect(primary).toHaveAttribute('aria-label', '停止')
    await expect(primary.locator('span')).toHaveClass(/bg-current/)

    await composer.click()
    await composer.type('please answer tersely')
    // Since "running is not busy" (3bc52fb9df) the primary keeps the Send
    // affordance mid-turn — steer is routed through the submit engine, not a
    // separate labeled button. Queue remains the explicit secondary action.
    await expect(primary).toHaveAttribute('aria-label', '发送')
    await expect(dictation).toBeVisible()
    await expect(speakReplies).toBeVisible()
    await expect(queue).toBeVisible()
    await expect(queue.locator('svg.tabler-icon-layers-intersect-2')).toBeVisible()
    const controlLabels = await page
      .locator('[data-slot="composer-root"] button')
      .evaluateAll(buttons => buttons.map(button => button.getAttribute('aria-label')))
    const speakRepliesIndex = controlLabels.findIndex(
      label => label === '朗读回复' || label === '停止朗读回复'
    )
    expect(controlLabels.indexOf('语音听写')).toBeLessThan(speakRepliesIndex)
    expect(speakRepliesIndex).toBeLessThan(controlLabels.indexOf('排队消息'))
    expect(controlLabels.indexOf('排队消息')).toBeLessThan(controlLabels.indexOf('发送'))
    await page.screenshot({ path: testInfo.outputPath('busy-composer-steer.png') })
    await expect(primary.locator('.codicon-arrow-up')).toBeVisible()

    await queue.click()
    await expect(primary).toHaveAttribute('aria-label', '停止')
    await expect(queue).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('busy-composer-queue.png') })
    await expect(page.getByText('1 条排队')).toBeVisible()

    await primary.click()
    await expect(page.getByText('1 条排队 — 已暂停')).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('busy-composer-queue-paused.png') })
  })
})
