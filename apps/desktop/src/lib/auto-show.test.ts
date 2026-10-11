import { describe, expect, it } from 'vitest'

import { type AutoShowEvent, createAutoShowLedger, decideAutoShow } from './auto-show'

const POSIX_WORKSPACE = '/home/me/project'
const WINDOWS_WORKSPACE = 'D:\\项目\\Stardust-Jarvis\\work'

function writeCompleted(name: string, result: unknown, args: Record<string, unknown> = {}): AutoShowEvent {
  return { payload: { args, name, result, tool_id: 'call-1' }, type: 'tool.complete' }
}

function agentOpen(url: string, label = ''): AutoShowEvent {
  return { payload: { label, url }, type: 'preview.open' }
}

describe('files the assistant writes', () => {
  it.each([
    'report.pdf',
    'index.html',
    'page.htm',
    'shot.png',
    'photo.JPG',
    'photo.jpeg',
    'loop.gif',
    'hero.webp',
    'logo.svg',
    'notes.md'
  ])('auto-shows a written %s inside the workspace', name => {
    const path = `${POSIX_WORKSPACE}/${name}`

    expect(
      decideAutoShow(
        writeCompleted('write_file', { bytes_written: 12, resolved_path: path }, { path }),
        POSIX_WORKSPACE
      )
    ).toEqual({
      open: true,
      origin: 'written-file',
      targets: [{ kind: 'file', path }]
    })
  })

  it('anchors a relative write to the session workspace', () => {
    expect(
      decideAutoShow(writeCompleted('write_file', { bytes_written: 3 }, { path: './dist/index.html' }), POSIX_WORKSPACE)
    ).toEqual({
      open: true,
      origin: 'written-file',
      targets: [{ kind: 'file', path: `${POSIX_WORKSPACE}/dist/index.html` }]
    })
  })

  it('matches a Windows workspace case-insensitively and keeps forward slashes', () => {
    const result = { bytes_written: 9, resolved_path: 'd:/项目/stardust-jarvis/work/report.pdf' }

    expect(decideAutoShow(writeCompleted('write_file', result), WINDOWS_WORKSPACE)).toEqual({
      open: true,
      origin: 'written-file',
      targets: [{ kind: 'file', path: 'd:/项目/stardust-jarvis/work/report.pdf' }]
    })
  })

  it.each(['main.py', 'notes.txt', 'data.json', 'setup.exe', 'run.ps1', 'bundle.zip', 'app.msi'])(
    'does not auto-show a written %s, because it is not on the previewable list',
    name => {
      const path = `${POSIX_WORKSPACE}/${name}`

      expect(decideAutoShow(writeCompleted('write_file', { resolved_path: path }), POSIX_WORKSPACE)).toEqual({
        open: false,
        reason: 'not-previewable'
      })
    }
  )

  it('refuses a written file outside the workspace, including climbing with ..', () => {
    expect(
      decideAutoShow(writeCompleted('write_file', { resolved_path: '/home/me/other/page.html' }), POSIX_WORKSPACE)
    ).toEqual({ open: false, reason: 'not-in-workspace' })
    expect(decideAutoShow(writeCompleted('write_file', {}, { path: '../escape.html' }), POSIX_WORKSPACE)).toEqual({
      open: false,
      reason: 'not-in-workspace'
    })
  })

  it('does not treat a sibling folder that shares the workspace name as inside it', () => {
    expect(
      decideAutoShow(writeCompleted('write_file', { resolved_path: '/home/me/project2/page.html' }), POSIX_WORKSPACE)
    ).toEqual({ open: false, reason: 'not-in-workspace' })
  })

  it.each([
    ['an error object', { error: 'Permission denied' }],
    ['a success:false receipt', { success: false, files_modified: ['/home/me/project/a.html'] }],
    ['a plain-text error message', 'Error: stale write refused']
  ])('does not open anything when the tool failed with %s', (_label, result) => {
    expect(decideAutoShow(writeCompleted('write_file', result, { path: 'a.html' }), POSIX_WORKSPACE)).toEqual({
      open: false,
      reason: 'failed-tool'
    })
  })

  it('reads every file a patch created or modified, and skips the ones it deleted', () => {
    const result = {
      files_created: [`${POSIX_WORKSPACE}/a.html`],
      files_deleted: [`${POSIX_WORKSPACE}/gone.html`],
      files_modified: [`${POSIX_WORKSPACE}/a.html`, `${POSIX_WORKSPACE}/b.md`, `${POSIX_WORKSPACE}/gone.html`],
      success: true
    }

    expect(decideAutoShow(writeCompleted('patch', result), POSIX_WORKSPACE)).toEqual({
      open: true,
      origin: 'written-file',
      targets: [
        { kind: 'file', path: `${POSIX_WORKSPACE}/a.html` },
        { kind: 'file', path: `${POSIX_WORKSPACE}/b.md` }
      ]
    })
  })

  it('keeps the previewable files of a mixed patch and drops the rest', () => {
    const result = { files_modified: [`${POSIX_WORKSPACE}/fix.py`, `${POSIX_WORKSPACE}/chart.png`], success: true }

    expect(decideAutoShow(writeCompleted('patch', result), POSIX_WORKSPACE)).toEqual({
      open: true,
      origin: 'written-file',
      targets: [{ kind: 'file', path: `${POSIX_WORKSPACE}/chart.png` }]
    })
  })

  it('never opens for reads or other tools, even when they name a previewable file', () => {
    expect(
      decideAutoShow(
        writeCompleted('read_file', { content: '<html></html>' }, { path: `${POSIX_WORKSPACE}/report.html` }),
        POSIX_WORKSPACE
      )
    ).toEqual({ open: false, reason: 'not-relevant' })
    expect(
      decideAutoShow(
        writeCompleted('image_generate', { success: true, image: 'https://cdn.example.com/x.png' }),
        POSIX_WORKSPACE
      )
    ).toEqual({ open: false, reason: 'not-relevant' })
  })

  it('refuses a malformed completion without throwing', () => {
    expect(decideAutoShow({ payload: null, type: 'tool.complete' }, POSIX_WORKSPACE)).toEqual({
      open: false,
      reason: 'not-relevant'
    })
    expect(
      decideAutoShow({ payload: { name: 'write_file', result: null }, type: 'tool.complete' }, POSIX_WORKSPACE)
    ).toEqual({
      open: false,
      reason: 'failed-tool'
    })
  })

  it('refuses everything when the session has no workspace to anchor to', () => {
    const result = { resolved_path: `${POSIX_WORKSPACE}/report.pdf` }

    expect(decideAutoShow(writeCompleted('write_file', result), '')).toEqual({ open: false, reason: 'no-workspace' })
    expect(decideAutoShow(writeCompleted('write_file', result), null)).toEqual({ open: false, reason: 'no-workspace' })
  })
})

describe('agent opens (preview.open)', () => {
  it('passes a localhost page through as an explicit open', () => {
    expect(decideAutoShow(agentOpen('http://localhost:5173/', 'Dev'), POSIX_WORKSPACE)).toEqual({
      open: true,
      origin: 'agent-open',
      targets: [{ kind: 'url', url: 'http://localhost:5173/' }]
    })
  })

  it('passes a remote page through when the assistant explicitly opens it', () => {
    expect(decideAutoShow(agentOpen('https://example.com/docs'), POSIX_WORKSPACE)).toEqual({
      open: true,
      origin: 'agent-open',
      targets: [{ kind: 'url', url: 'https://example.com/docs' }]
    })
  })

  it('never reads a URL out of message text, tool output, or web search results', () => {
    expect(
      decideAutoShow({ payload: { text: 'See https://example.com/x' }, type: 'message.delta' }, POSIX_WORKSPACE)
    ).toEqual({ open: false, reason: 'not-relevant' })
    expect(
      decideAutoShow(
        writeCompleted('web_search', { results: [{ url: 'https://example.com/a.html' }] }),
        POSIX_WORKSPACE
      )
    ).toEqual({ open: false, reason: 'not-relevant' })
  })

  it('opens a non-previewable workspace file by explicit request, such as a CSV', () => {
    expect(decideAutoShow(agentOpen(`${POSIX_WORKSPACE}/data.csv`), POSIX_WORKSPACE)).toEqual({
      open: true,
      origin: 'agent-open',
      targets: [{ kind: 'file', path: `${POSIX_WORKSPACE}/data.csv` }]
    })
  })

  it.each(['setup.exe', 'run.ps1', 'install.msi', 'bundle.zip', 'hook.sh', 'script.py', 'shortcut.lnk', 'app.js'])(
    'never opens a %s, which is a program, script, installer or archive',
    name => {
      expect(decideAutoShow(agentOpen(`${POSIX_WORKSPACE}/${name}`), POSIX_WORKSPACE)).toEqual({
        open: false,
        reason: 'blocked-type'
      })
    }
  )

  it('refuses a file outside the workspace, and a home-relative path it cannot anchor', () => {
    expect(decideAutoShow(agentOpen('/tmp/artifact.html'), POSIX_WORKSPACE)).toEqual({
      open: false,
      reason: 'not-in-workspace'
    })
    expect(decideAutoShow(agentOpen('~/notes.md'), POSIX_WORKSPACE)).toEqual({
      open: false,
      reason: 'not-in-workspace'
    })
  })

  it('accepts file:// URLs inside the workspace and refuses those outside it', () => {
    expect(decideAutoShow(agentOpen('file:///home/me/project/index.html'), POSIX_WORKSPACE)).toEqual({
      open: true,
      origin: 'agent-open',
      targets: [{ kind: 'file', path: '/home/me/project/index.html' }]
    })
    expect(decideAutoShow(agentOpen('file:///etc/passwd'), POSIX_WORKSPACE)).toEqual({
      open: false,
      reason: 'not-in-workspace'
    })
  })

  it.each(['javascript:alert(1)', 'data:text/html,<b>hi</b>', 'localhost:3000'])(
    'refuses the non-file target %s',
    url => {
      expect(decideAutoShow(agentOpen(url), POSIX_WORKSPACE)).toEqual({ open: false, reason: 'unsupported-target' })
    }
  )

  it('refuses an empty target', () => {
    expect(decideAutoShow(agentOpen('   '), POSIX_WORKSPACE)).toEqual({ open: false, reason: 'no-path' })
  })
})

describe('auto-show ledger', () => {
  it('claims a file once per live turn and again in the next turn', () => {
    const ledger = createAutoShowLedger()

    expect(ledger.isLive('s1')).toBe(false)

    ledger.begin('s1')
    expect(ledger.isLive('s1')).toBe(true)
    expect(ledger.claim('s1', 'file:/w/a.html')).toBe(true)
    expect(ledger.claim('s1', 'file:/w/a.html')).toBe(false)
    expect(ledger.claim('s1', 'file:/w/b.html')).toBe(true)

    ledger.end('s1')
    expect(ledger.isLive('s1')).toBe(false)

    ledger.begin('s1')
    expect(ledger.claim('s1', 'file:/w/a.html')).toBe(true)
  })

  it('keeps sessions apart', () => {
    const ledger = createAutoShowLedger()

    ledger.begin('s1')
    ledger.begin('s2')
    expect(ledger.claim('s1', 'file:/w/a.html')).toBe(true)
    expect(ledger.claim('s2', 'file:/w/a.html')).toBe(true)

    ledger.end('s1')
    expect(ledger.isLive('s2')).toBe(true)
    expect(ledger.claim('s2', 'file:/w/a.html')).toBe(false)
  })
})
