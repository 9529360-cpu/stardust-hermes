/**
 * Mid-turn commentary must never settle AFTER the final reply, on the
 * Responses/Codex wire.
 *
 * Responses/Codex providers stream a tool-using turn's narration as a
 * `phase: "commentary"` message item beside the tool calls. The backend persists
 * it on the reasoning channel and leaves the assistant row's `content` empty, so
 * once the turn commits, the stored transcript carries that narration as
 * reasoning, never as visible text. The desktop used to look for a sealed live
 * interim bubble only in the committed rows' visible text; every bubble looked
 * uncommitted and the reconcile appended it after the final reply, again on every
 * later reconcile. The thread then ended on a sentence like "next I will count
 * the files" with nothing after it, while the real continuation sat above, and
 * the leftovers of earlier turns kept drifting to the end.
 *
 * `interim-messages.spec.ts` cannot see this: the chat-completions mock persists
 * interim text in `content`. This spec drives the real backend through a
 * Responses-API streaming provider instead.
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import * as fs from 'node:fs'
import * as path from 'node:path'

import {
  startResponsesMock,
  type ResponsesMock,
  type ResponsesStep
} from '../../../tests-js/scripts/mock-responses-server'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  type Sandbox,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { type ElectronApplication, expect, type Page, test } from './test'

const SCRIPT = 'commentary-order'

const COMMENTARY = [
  'Step one: I will read the system clock before anything else.',
  'Step two: I will check how much space is free on the system drive.',
  'Step three: I will count the files in the working directory.'
] as const

const finalAnswer = (turn: number): string => `All three checks are finished for turn ${turn}.`

/**
 * One narration + one real tool call per step, three times, then the final
 * answer. The step index is the number of tool results already in the history.
 */
function commentaryScript(turn: number): ResponsesStep[] {
  return [
    ...COMMENTARY.map((commentary, index): ResponsesStep => ({
      commentary,
      reasoning: index === 0 ? 'Planning the three checks the user asked for.' : undefined,
      toolCalls: [{ args: { command: `echo commentary-step-${index + 1}` }, name: 'terminal' }]
    })),
    { text: finalAnswer(turn) }
  ]
}

interface CommentaryFixture {
  app: ElectronApplication
  cleanup: () => Promise<void>
  mock: ResponsesMock
  page: Page
  sandbox: Sandbox
}

async function setupFixture(): Promise<CommentaryFixture> {
  // The script is keyed by name, and both turns use the same shape; the final
  // answer differs per turn through a second script entry.
  const mock = await startResponsesMock({
    scripts: { [`${SCRIPT}-1`]: commentaryScript(1), [`${SCRIPT}-2`]: commentaryScript(2) }
  })

  const sandbox = createSandbox('commentary-order')
  writeMockProviderConfig(sandbox.hermesHome, mock.url)

  // The shared writer only emits chat completions; this provider speaks the
  // Responses wire, which is what puts commentary on the reasoning channel.
  const configPath = path.join(sandbox.hermesHome, 'config.yaml')
  const config = fs.readFileSync(configPath, 'utf8')

  expect(config).toContain('api_mode: chat_completions')
  fs.writeFileSync(configPath, config.replace('api_mode: chat_completions', 'api_mode: codex_responses'), 'utf8')

  writeEnvFile(sandbox.hermesHome)

  const { app, page } = await launchDesktop(buildAppEnv(sandbox))

  return {
    app,
    cleanup: async () => {
      await app.close().catch(() => undefined)
      await mock.close().catch(() => undefined)
      sandbox.cleanup()
    },
    mock,
    page,
    sandbox
  }
}

async function send(page: Page, text: string): Promise<void> {
  const composer = page.locator('[contenteditable="true"]').first()

  await composer.waitFor({ state: 'visible', timeout: 30_000 })
  await composer.click()
  await composer.type(text, { delay: 5 })
  await page.keyboard.press('Enter')
}

function threadText(page: Page): Promise<string> {
  return page.evaluate(() => document.querySelector('[data-slot="aui_thread-viewport"]')?.textContent ?? '')
}

/**
 * Commentary the thread shows AFTER the last occurrence of `finalText`. Empty
 * is the invariant. An occurrence before it (the live layout, or the
 * collapsed reasoning rows once the turn converges) is fine.
 */
function commentaryAfterFinal(text: string, finalText: string): string[] {
  const finalIndex = text.lastIndexOf(finalText)

  return COMMENTARY.filter(commentary => text.lastIndexOf(commentary) > finalIndex)
}

test.describe('Responses/Codex commentary ordering', () => {
  test.describe.configure({ mode: 'serial' })

  let fixture: CommentaryFixture

  test.beforeAll(async () => {
    fixture = await setupFixture()
    await waitForAppReady(fixture, 120_000)
  })

  test.afterAll(async () => {
    await fixture?.cleanup()
  })

  for (const turn of [1, 2]) {
    test(`turn ${turn}: the final reply stays last and no commentary follows it`, async () => {
      // The first message of a fresh app waits in the composer until the agent
      // finishes building, which takes minutes on a cold runner.
      test.setTimeout(360_000)

      const { page } = fixture
      const final = finalAnswer(turn)

      await send(page, `E2E_RESPONSES(${SCRIPT}-${turn}) run the three checks`)

      await page.waitForFunction(
        text => (document.querySelector('[data-slot="aui_thread-viewport"]')?.textContent ?? '').includes(text),
        final,
        { timeout: 240_000 }
      )

      // The stored-history reconcile lands shortly after the turn completes
      // (sessions.changed -> hydrate). Sample the thread across that window:
      // the invariant has to hold at EVERY point after the final reply shows,
      // not just once the transcript happens to have settled.
      const violations: Array<{ afterMs: number; commentary: string[] }> = []
      const startedAt = Date.now()

      while (Date.now() - startedAt < 4_000) {
        const leaked = commentaryAfterFinal(await threadText(page), final)

        if (leaked.length > 0) {
          violations.push({ afterMs: Date.now() - startedAt, commentary: leaked })
        }

        await page.waitForTimeout(150)
      }

      expect(violations, 'commentary must never be rendered after the final reply').toEqual([])

      // Not vacuous: the final answer is on screen, and an earlier turn's
      // final answer is still above it rather than displaced below.
      const text = await threadText(page)

      expect(text).toContain(final)

      if (turn > 1) {
        expect(text.indexOf(finalAnswer(1))).toBeGreaterThanOrEqual(0)
        expect(text.indexOf(finalAnswer(1))).toBeLessThan(text.lastIndexOf(final))
      }
    })
  }
})
