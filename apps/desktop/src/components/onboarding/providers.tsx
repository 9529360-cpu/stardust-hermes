import { RowButton } from '@/components/ui/row-button'
import { useI18n } from '@/i18n'
import { ChevronRight } from '@/lib/icons'
import type { OAuthProvider } from '@/types/hermes'

// Display names for legacy OAuth providers whose sign-in flow can still be
// shown as compatibility state (flow.tsx); Desktop no longer lists them.
const PROVIDER_TITLES: Record<string, string> = {
  nous: 'Nous Research',
  'openai-codex': 'ChatGPT or Codex Subscription',
  'minimax-oauth': 'MiniMax',
  'qwen-oauth': 'Qwen Code',
  'xai-oauth': 'xAI Grok',
  anthropic: 'Anthropic API Key',
  'claude-code': 'Anthropic OAuth: Required Extra Usage Credits to Use Subscription'
}

export const providerTitle = (p: OAuthProvider) => PROVIDER_TITLES[p.id] ?? p.name

const PROVIDER_ROW_CLASS =
  'group flex w-full items-center justify-between gap-3 rounded-[6px] px-3 py-2.5 text-left transition-colors hover:bg-(--ui-control-hover-background)'

function KeyProviderRow({ onClick, pitch, title }: { onClick: () => void; pitch: string; title: string }) {
  return (
    <RowButton className={PROVIDER_ROW_CLASS} onClick={onClick}>
      <div className="min-w-0">
        <span className="text-[length:var(--conversation-text-font-size)] font-semibold">{title}</span>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{pitch}</p>
      </div>
      <ChevronRight className="size-4 text-muted-foreground transition group-hover:text-foreground" />
    </RowButton>
  )
}

/** Onboarding row for the managed local runtime: no account, no key — the
 *  destination is the Models page, where install/download live. */
export function LocalModelsProviderRow({ onClick }: { onClick: () => void }) {
  const { t } = useI18n()

  return (
    <KeyProviderRow onClick={onClick} pitch={t.onboarding.localModelsPitch} title={t.onboarding.localModelsTitle} />
  )
}
