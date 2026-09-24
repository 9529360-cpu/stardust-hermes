import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'

import { Intro } from './intro'

function renderIntro(locale: 'en' | 'zh' = 'zh') {
  return render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <Intro personality="helpful" seed={1} />
    </I18nProvider>
  )
}

describe('personal assistant intro', () => {
  it('keeps the empty conversation quiet and immediately readable', () => {
    renderIntro('zh')

    expect(screen.getByText('今天想做什么？')).toBeTruthy()
    expect(screen.getByText(/需要项目、文件或预览时/)).toBeTruthy()
    expect(screen.queryByText('你的个人助理')).toBeNull()
    expect(screen.getByTestId('assistant-quick-actions')).toBeTruthy()
    expect(screen.getByRole('button', { name: '从一条消息开始' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '浏览命令' })).toBeTruthy()
  })

  it('keeps the quiet empty state localized in English', () => {
    renderIntro('en')

    expect(screen.getByText('What should we work on?')).toBeTruthy()
    expect(screen.getByText(/Project, file, or preview context/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Start with a message' })).toBeTruthy()
  })
})
