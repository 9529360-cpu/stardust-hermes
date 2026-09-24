import { openCommandPalette } from '@/store/command-palette'
import { requestComposerFocus } from '@/app/chat/composer/focus'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'

export type IntroProps = {
  personality?: string
  seed?: number
}

export function Intro({ personality: _personality, seed: _seed }: IntroProps) {
  const { t } = useI18n()
  const assistant = t.assistant.intro

  return (
    <div className="assistant-home" data-slot="aui_intro">
      <div className="assistant-home-copy">
        <span className="assistant-home-eyebrow">{assistant.eyebrow}</span>
        <h1>{assistant.headline}</h1>
        <p>{assistant.body}</p>
      </div>
      <div className="assistant-home-actions" data-testid="assistant-quick-actions">
        <Button onClick={() => requestComposerFocus()} size="sm" variant="default">
          {assistant.start}
        </Button>
        <Button onClick={openCommandPalette} size="sm" variant="outline">
          {assistant.actions}
        </Button>
      </div>
    </div>
  )
}
