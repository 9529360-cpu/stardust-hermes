import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { createDesktopLogger } from './desktop-logger'

const directories: string[] = []
const loggers: ReturnType<typeof createDesktopLogger>[] = []

function fixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'w13-logger-'))
  directories.push(dir)
  const file = path.join(dir, 'logs', 'desktop.log')
  const logger = createDesktopLogger(file)
  loggers.push(logger)

  return { file, logger }
}

afterEach(() => {
  for (const logger of loggers.splice(0)) {logger.stopDesktopLogFlushTimer()}
  vi.useRealTimers()

  for (const dir of directories.splice(0)) {fs.rmSync(dir, { recursive: true, force: true })}
})

describe('desktop logger', () => {
  it('keeps the recent tail and flushes all buffered lines on shutdown', () => {
    const { file, logger } = fixture()
    logger.rememberLog('')
    expect(logger.hermesLog).toEqual([])
    logger.rememberLog(Array.from({ length: 305 }, (_, i) => `line ${i}`).join('\r\n'))
    expect(logger.hermesLog).toHaveLength(300)
    expect(logger.hermesLog[0]).toContain('line 5')
    logger.stopDesktopLogFlushTimer()
    logger.flushDesktopLogBufferSync()
    const text = fs.readFileSync(file, 'utf8')
    expect(text.trim().split('\n')).toHaveLength(305)
    expect(text).toContain('line 304')
    logger.flushDesktopLogBufferSync()
    expect(fs.readFileSync(file, 'utf8')).toBe(text)
  })

  it('flushes after the existing 120ms delay', async () => {
    vi.useFakeTimers()
    const { file, logger } = fixture()
    logger.rememberLog('delayed line')
    expect(fs.existsSync(file)).toBe(false)
    await vi.advanceTimersByTimeAsync(120)
    await vi.waitFor(() => expect(fs.readFileSync(file, 'utf8')).toContain('delayed line'))
  })

  it.each([10 * 1024 * 1024, 40 * 1024 * 1024 + 1])('rotates or discards a %i-byte live log', size => {
    const { file, logger } = fixture()
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, 'old live')
    fs.truncateSync(file, size)

    for (let i = 1; i <= 3; i++) {fs.writeFileSync(`${file}.${i}`, `backup ${i}`)}
    logger.rememberLog('new line')
    logger.stopDesktopLogFlushTimer()
    logger.flushDesktopLogBufferSync()
    expect(fs.readFileSync(file, 'utf8')).toContain('new line')

    if (size === 10 * 1024 * 1024) {
      expect(fs.statSync(`${file}.1`).size).toBe(size)
      expect(fs.readFileSync(`${file}.2`, 'utf8')).toBe('backup 1')
      expect(fs.readFileSync(`${file}.3`, 'utf8')).toBe('backup 2')
    } else {
      for (let i = 1; i <= 3; i++) {expect(fs.existsSync(`${file}.${i}`)).toBe(false)}
    }
  })
})
