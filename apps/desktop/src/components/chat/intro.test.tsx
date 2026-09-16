import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { Intro } from './intro'

vi.mock('@/app/chat/composer/focus', () => ({ requestComposerInsert: vi.fn() }))

function renderIntro(locale: 'en' | 'zh' = 'zh') {
  return render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <Intro personality="helpful" seed={1} />
    </I18nProvider>
  )
}

describe('personal assistant intro', () => {
  it('presents a calm localized assistant home instead of the developer wordmark', () => {
    renderIntro('zh')

    expect(screen.getByText('你的个人助理')).toBeTruthy()
    expect(screen.getByText('今天想让我帮你做什么？')).toBeTruthy()
    expect(screen.queryByText('HERMES AGENT')).toBeNull()
    expect(screen.getByTestId('assistant-quick-actions').children).toHaveLength(3)
  })

  it('keeps the assistant home localized in English', () => {
    renderIntro('en')

    expect(screen.getByText('Your personal assistant')).toBeTruthy()
    expect(screen.getByText('What can I take care of?')).toBeTruthy()
  })
})
