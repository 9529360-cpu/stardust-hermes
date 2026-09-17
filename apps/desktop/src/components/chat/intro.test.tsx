import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { Intro } from './intro'

vi.mock('@/app/chat/composer/focus', () => ({ requestComposerInsert: vi.fn() }))

function renderIntro(locale: 'en' | 'zh' = 'zh', cwd?: string) {
  return render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <Intro cwd={cwd} personality="helpful" seed={1} />
    </I18nProvider>
  )
}

describe('developer workspace intro', () => {
  it('presents a localized developer task home', () => {
    renderIntro('zh')

    expect(screen.getByText('Stardust 助理')).toBeTruthy()
    expect(screen.getByText('今天想让我帮你做什么？')).toBeTruthy()
    expect(screen.queryByText('HERMES AGENT')).toBeNull()
    expect(screen.getByTestId('assistant-quick-actions').children).toHaveLength(3)
  })

  it('keeps the developer workspace localized in English', () => {
    renderIntro('en')

    expect(screen.getByText('Stardust assistant')).toBeTruthy()
    expect(screen.getByText('What can I help with?')).toBeTruthy()
  })

  it('switches to project-scoped copy when a cwd is selected', () => {
    renderIntro('zh', 'D:/work/project')

    expect(screen.getByText('这个项目要做什么？')).toBeTruthy()
    expect(screen.queryByText('今天要改哪个项目？')).toBeNull()
  })
})
