/**
 * A side-chat a bot's workspace opens with "+" is an ordinary conversation: once
 * its first turn is saved it is listed in the sidebar. The bot's own Bot Chat
 * stays hidden. The list is read back after a reload, so only the backend's own
 * listing can put the side-chat there.
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { MOCK_REPLY } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

type Page = MockBackendFixture['page']

// The side-chat's first message. A row shows it while the chat has no title.
const SIDE_TEXT = 'hello side chat'

// BOT_SIDEBAR_SCREENSHOT_DIR=<dir> with BOT_SIDEBAR_SCREENSHOT_LABEL=<before|after>
// saves the window and the state DB rows for review; never part of the assertions.
function evidencePath(name: string): string | null {
  const dir = process.env.BOT_SIDEBAR_SCREENSHOT_DIR

  if (!dir) {
    return null
  }

  fs.mkdirSync(dir, { recursive: true })

  return path.join(dir, `${process.env.BOT_SIDEBAR_SCREENSHOT_LABEL ?? 'run'}-${name}`)
}

// The registry title of a bot's canonical chat.
const BOT_CHAT_TITLE = 'Bot Chat'

interface StoredRow {
  hidden: number
  id: string
  title: string | null
}

// One SQLite file under the sandbox HERMES_HOME: its session rows and the
// message count per session.
interface DbFile {
  messages: Array<[string, unknown]>
  path: string
  sessions: Array<[string, string | null, number, number]>
}

// Every .db file under this run's HERMES_HOME, read with the repo backend's
// interpreter. Read-only: nothing is written.
function dbReport(hermesHome: string): DbFile[] {
  const python = process.env.HERMES_DESKTOP_PYTHON

  if (!python) {
    throw new Error('HERMES_DESKTOP_PYTHON must name the repo backend interpreter')
  }

  const script = [
    'import json, os, sqlite3, sys',
    'out = []',
    'for root, _dirs, files in os.walk(sys.argv[1]):',
    '    for name in files:',
    '        if not name.endswith(".db"):',
    '            continue',
    '        file = os.path.join(root, name)',
    '        con = sqlite3.connect(file)',
    '        try:',
    '            sessions = con.execute("SELECT id, title, hidden, started_at FROM sessions ORDER BY started_at").fetchall()',
    '        except sqlite3.Error as error:',
    '            sessions = [["error", str(error), -1, -1]]',
    '        try:',
    '            messages = con.execute("SELECT session_id, COUNT(*) FROM messages GROUP BY session_id").fetchall()',
    '        except sqlite3.Error as error:',
    '            messages = [["error", str(error)]]',
    '        out.append({"path": file, "sessions": sessions, "messages": messages})',
    'print(json.dumps(out))'
  ].join('\n')

  return JSON.parse(execFileSync(python, ['-c', script, hermesHome], { encoding: 'utf8' }))
}

// The session rows of the default profile's state DB. Matched by exact file name:
// the sandbox also holds shared-state.db, which a suffix match would take.
function stateRows(report: DbFile[]): StoredRow[] {
  const state = report.find(file => path.basename(file.path) === 'state.db')

  return (state?.sessions ?? []).map(([id, title, hidden]) => ({ hidden, id, title }))
}

async function openDefaultBot(page: Page): Promise<void> {
  const roster = page.locator('[data-slot="bots-roster"]')

  await page
    .getByRole('button', { name: /智能体空间|Agent space/ })
    .first()
    .click()
  await expect(roster).toBeVisible({ timeout: 20_000 })

  // The default profile is the first bot row on a fresh install.
  await page.locator('[data-slot="bots-roster"] [data-roster-key]').first().click()
  await expect(roster).toBeHidden({ timeout: 30_000 })
  await expect(page.locator('[data-slot="bot_chat_empty"]')).toBeVisible({ timeout: 60_000 })
}

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('a bot side-chat stays listed after a reload, and the Bot Chat stays hidden', async () => {
  test.setTimeout(300_000)
  const page = fixture!.page
  await waitForAppReady(fixture!, 120_000)

  await openDefaultBot(page)

  // "+" in the bot's workspace opens a side-chat draft tile beside the Bot Chat.
  // The Bot Chat has a composer on screen too, so the text goes to whichever
  // composer holds the keyboard after the draft opens. Click none of them.
  await page.keyboard.press('Control+t')
  await expect(page.getByRole('tab', { name: /New session|尚未发送/, selected: true }).first()).toBeVisible({
    timeout: 15_000
  })

  const typingInComposer = await page.evaluate(
    () => Boolean(document.activeElement?.closest('[data-slot="composer-root"]'))
  )

  expect(typingInComposer, 'the new side-chat draft must hold keyboard focus').toBe(true)

  await page.keyboard.type(SIDE_TEXT)
  await page.keyboard.press('Enter')

  // The send moves focus to a fresh draft tab, so the reply is only on screen once
  // the side-chat tile is focused again. Wait for the tile to carry the message,
  // focus it, and wait for its reply.
  const sideTab = page.getByRole('tab', { name: SIDE_TEXT }).first()
  await expect(sideTab).toBeVisible({ timeout: 60_000 })
  await sideTab.click()
  await expect(page.getByText(MOCK_REPLY).filter({ visible: true }).first()).toBeVisible({ timeout: 60_000 })

  // The databases as the turn left them, before the tile closes.
  const beforeClose = dbReport(fixture!.sandbox.hermesHome)

  // A tile keeps its session listed, so close it: the list must then come from
  // the backend alone. A middle-click closes a tab.
  await sideTab.click({ button: 'middle' })
  await expect(sideTab).toHaveCount(0, { timeout: 15_000 })

  await page.reload()
  await waitForAppReady(fixture!, 120_000)

  // The databases as the reload found them. Written before the assertions, so a
  // run that fails still leaves the evidence behind.
  const afterReload = dbReport(fixture!.sandbox.hermesHome)
  const stored = stateRows(afterReload)
  const evidence = evidencePath('side-chat-state.json')

  if (evidence) {
    fs.writeFileSync(evidence, JSON.stringify({ beforeClose, afterReload }, null, 2))
  }

  const sidebar = page.locator('[data-tour="sessions-sidebar"]')
  const rows = sidebar.locator('.group.row-hover')

  try {
    await expect(rows).toHaveCount(1, { timeout: 30_000 })
    await expect(rows.first()).toContainText(SIDE_TEXT)
  } finally {
    const shot = evidencePath('side-chat-listed.png')

    if (shot) {
      await page.screenshot({ path: shot })
    }
  }

  const side = stored.filter(row => row.title !== BOT_CHAT_TITLE)
  expect(side).toHaveLength(1)
  expect(side[0]?.hidden).toBe(0)
  expect(stored.find(row => row.title === BOT_CHAT_TITLE)?.hidden).toBe(1)
})
