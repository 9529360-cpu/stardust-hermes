import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import { TRANSLATIONS } from '@/i18n/catalog'
import type { Locale } from '@/i18n/types'
import { KNOWN_TASK_STATES, type TrackedTaskRow } from '@/lib/task-model'

import { TaskRow } from './index'

vi.mock('../cron', () => ({ CronView: () => null }))

const task = (overrides: Partial<TrackedTaskRow> & { statusPhase?: string } = {}) => ({
  id: 'task:copy',
  title: '',
  rail: 'delegation',
  state: 'interrupted',
  phase: { kind: 'none' } as const,
  observedAt: 1,
  seq: 0,
  ...overrides
})

function renderRow(row = task(), locale: Locale = 'zh', onOpen = vi.fn()) {
  return render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <ul>
        <TaskRow onOpen={onOpen} row={row} />
      </ul>
    </I18nProvider>
  )
}

describe('Task Center localized row copy', () => {
  it.each([
    ['retry', '重试'],
    ['review_or_retry', '检查或重试'],
    ['resume', '恢复']
  ])('renders recovery action %s without exposing the protocol token', (recoveryAction, label) => {
    const onOpen = vi.fn()
    renderRow(task({ recoverable: true, recoveryAction }), 'zh', onOpen)
    const row = screen.getByRole('button')
    expect(row.textContent).toContain('委派任务')
    expect(row.textContent).toContain('会话不可用 · 已中断')
    expect(row.textContent).toContain(`可恢复 (${label})`)
    expect(row.textContent).not.toMatch(/Session unavailable|retry|resume/)
    fireEvent.click(row)
    expect(onOpen).toHaveBeenCalledOnce()
  })

  it.each([
    [{ kind: 'working-on', tool: 'terminal' }, '正在使用 terminal'],
    [{ kind: 'exit-code', code: 127 }, '退出码 127'],
    [{ kind: 'needs-secret', name: 'API_KEY' }, '需要密钥：API_KEY'],
    [{ kind: 'needs-vault', name: 'KeePass' }, '解锁密码管理器：KeePass']
  ] as const)('renders structured phase %j with translated framing', (phase, expected) => {
    renderRow(task({ phase }))
    expect(screen.getByRole('button').textContent).toContain(expected)
    expect(screen.getByRole('button').textContent).not.toMatch(/Working on|Exit code/)
  })

  it.each([
    ['running', '执行中'],
    ['stalling', '等待进展'],
    ['finalizing', '正在收尾'],
    ['timeout', '已超时'],
    ['stalled', '进展停滞'],
    ['unknown', '状态不可用'],
    ['scheduled', '已安排'],
    ['last_failed', '上次运行失败'],
    ['error', '失败'],
    ['done', '已完成'],
    ['waiting', '等待中'],
    ['active', '进行中'],
    ['claimed', '已领取待执行'],
    ['delivery_failed', '结果发送失败'],
    ['skipped', '已跳过']
  ])('translates durable protocol phase %s', (statusPhase, expected) => {
    renderRow(task({ statusPhase }))
    expect(screen.getByRole('button').textContent).toContain(` · ${expected}`)
    expect(screen.getByRole('button').textContent).not.toContain(` · ${statusPhase}`)
  })

  it('keeps user progress text and unknown backend values readable without implying a known action', () => {
    const { unmount } = renderRow(task({ phase: { kind: 'text', text: 'running' } }))
    expect(screen.getByRole('button').textContent).toContain(' · running')
    unmount()
    renderRow(
      task({
        rail: 'future_rail',
        state: 'future_state',
        statusPhase: 'future_phase',
        recoverable: true,
        recoveryAction: 'future_action'
      })
    )
    const text = screen.getByRole('button').textContent
    expect(text).toContain('任务（future_rail）')
    expect(text).toContain('未知状态（future_state）')
    expect(text).toContain('未知状态（future_phase）')
    expect(text).toContain('其他恢复方式（future_action）')
    expect(text).not.toContain('(恢复)')
  })

  it('uses the selected bundle for every supported locale and translates every known task state', () => {
    for (const locale of Object.keys(TRANSLATIONS) as Locale[]) {
      const copy = TRANSLATIONS[locale].taskCenter
      const { unmount } = renderRow(task({ requiresApproval: true }), locale)
      expect(screen.getByRole('button').textContent).toContain(copy.sessionUnavailable)
      expect(screen.getByRole('button').textContent).toContain(copy.approvalRequired)

      for (const state of KNOWN_TASK_STATES) {
        expect(copy.states[state]).toBeTruthy()

        if (locale !== 'en') {expect(copy.states[state]).not.toBe(TRANSLATIONS.en.taskCenter.states[state])}
      }

      if (locale !== 'en') {
        expect(copy.sessionUnavailable).not.toBe(TRANSLATIONS.en.taskCenter.sessionUnavailable)

        for (const key of Object.keys(copy.phases) as (keyof typeof copy.phases)[]) {
          expect(copy.phases[key]).not.toBe(TRANSLATIONS.en.taskCenter.phases[key])
        }
      }

      unmount()
    }
  })
})
