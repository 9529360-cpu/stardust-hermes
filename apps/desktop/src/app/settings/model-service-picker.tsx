import type { ModelOptionProvider } from '@hermes/shared'
import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useI18n } from '@/i18n'
import { Check, ChevronRight, Cpu, KeyRound, Plus, Settings2 } from '@/lib/icons'
import { isSubmitEnter } from '@/lib/ime'
import { cn } from '@/lib/utils'

import { CONTROL_TEXT } from './constants'
import { Pill, SectionHeading } from './primitives'

export type ModelConnectionView = 'accounts' | 'custom-endpoints' | 'keys'

export function isModelServiceReady(provider?: ModelOptionProvider): boolean {
  return !!provider && (provider.authenticated !== false || (provider.models?.length ?? 0) > 0)
}

interface ModelServicePickerProps {
  activating: boolean
  apiKeyDraft: string
  applying: boolean
  currentModel: { model: string; provider: string } | null
  onActivateApiKey: () => void
  onAddCustomService: () => void
  onAddService: () => void
  onApiKeyChange: (value: string) => void
  onApply: () => void
  onOpenConnectionView: (view: ModelConnectionView) => void
  onSelectModel: (model: string) => void
  onSelectProvider: (provider: ModelOptionProvider) => void
  onSetupProvider: () => void
  providers: readonly ModelOptionProvider[]
  selectedModel: string
  selectedProvider: string
  selectedProviderModels: readonly string[]
  selectedProviderRow?: ModelOptionProvider
  setupIsApiKey: boolean
}

export function ModelServicePicker({
  activating,
  apiKeyDraft,
  applying,
  currentModel,
  onActivateApiKey,
  onAddCustomService,
  onAddService,
  onApiKeyChange,
  onApply,
  onOpenConnectionView,
  onSelectModel,
  onSelectProvider,
  onSetupProvider,
  providers,
  selectedModel,
  selectedProvider,
  selectedProviderModels,
  selectedProviderRow,
  setupIsApiKey
}: ModelServicePickerProps) {
  const { t } = useI18n()
  const m = t.settings.model
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const orderedProviders = useMemo(() => {
    const rows = [...providers]
    const currentIndex = rows.findIndex(provider => provider.slug === currentModel?.provider)

    if (currentIndex > 0) {
      const [current] = rows.splice(currentIndex, 1)
      rows.unshift(current)
    }

    return rows
  }, [currentModel?.provider, providers])

  const ready = isModelServiceReady(selectedProviderRow)
  const apiKeyProvider = selectedProviderRow?.auth_type === 'api_key' && !!selectedProviderRow.key_env
  const selectedModels =
    selectedModel && !selectedProviderModels.includes(selectedModel)
      ? [selectedModel, ...selectedProviderModels]
      : selectedProviderModels

  return (
    <section>
      <SectionHeading icon={Cpu} title={m.primaryTitle} />
      <p className="mb-3 text-xs text-muted-foreground">{m.appliesDesc}</p>

      {orderedProviders.length > 0 ? (
        <div className="grid gap-1">
          {orderedProviders.map(provider => {
            const isCurrent = provider.slug === currentModel?.provider
            const isSelected = provider.slug === selectedProvider
            const providerReady = isModelServiceReady(provider)

            return (
              <div
                className={cn('rounded-lg transition-colors', isSelected && 'bg-(--ui-bg-quaternary)')}
                key={provider.slug}
              >
                <button
                  className="group flex w-full items-center gap-3 px-2 py-3 text-left"
                  onClick={() => onSelectProvider(provider)}
                  type="button"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium">{provider.name}</span>
                      {isCurrent ? (
                        <Pill tone="primary">
                          <Check className="size-3" />
                          {m.currentService}
                        </Pill>
                      ) : providerReady ? (
                        <Pill tone="success">{t.settings.providers.connected}</Pill>
                      ) : (
                        <Pill>{m.serviceNotConnected}</Pill>
                      )}
                    </div>
                    <div className="mt-1 truncate text-xs text-muted-foreground">
                      {isCurrent && currentModel?.model
                        ? `${m.model}: ${currentModel.model}`
                        : providerReady
                          ? m.modelCount(provider.models?.length ?? 0)
                          : m.serviceNotConnected}
                    </div>
                  </div>
                  <ChevronRight
                    className={cn(
                      'size-4 shrink-0 text-muted-foreground transition-transform',
                      isSelected && 'rotate-90'
                    )}
                  />
                </button>

                {isSelected && (
                  <div className="grid gap-3 px-2 pb-3">
                    {!ready ? (
                      setupIsApiKey ? (
                        <div className="grid gap-2 @2xl:grid-cols-[minmax(0,1fr)_auto]">
                          <Input
                            autoComplete="off"
                            className={CONTROL_TEXT}
                            onChange={event => onApiKeyChange(event.target.value)}
                            onKeyDown={event => {
                              if (isSubmitEnter(event)) {
                                onActivateApiKey()
                              }
                            }}
                            placeholder={m.apiKeyPlaceholder}
                            type="password"
                            value={apiKeyDraft}
                          />
                          <Button disabled={!apiKeyDraft.trim() || activating} onClick={onActivateApiKey} size="sm">
                            {activating ? t.common.connecting : t.common.connect}
                          </Button>
                        </div>
                      ) : (
                        <div className="flex flex-wrap items-center gap-2">
                          <Button onClick={onSetupProvider} size="sm">
                            {m.connectProvider(selectedProviderRow?.name ?? m.provider)}
                          </Button>
                          {selectedProviderRow && (
                            <span className="text-xs text-muted-foreground">
                              {selectedProviderRow.auth_type === 'api_key'
                                ? m.setupApiKeyHint(selectedProviderRow.name)
                                : m.setupOauthHint(selectedProviderRow.name)}
                            </span>
                          )}
                        </div>
                      )
                    ) : (
                      <>
                        {apiKeyProvider && (
                          <label className="grid gap-1.5 text-xs text-muted-foreground">
                            {m.apiKeyLabel}
                            <Input
                              autoComplete="off"
                              className={CONTROL_TEXT}
                              onChange={event => onApiKeyChange(event.target.value)}
                              placeholder={m.apiKeyKeepExisting}
                              type="password"
                              value={apiKeyDraft}
                            />
                          </label>
                        )}
                        <div className="flex flex-wrap items-center gap-2">
                          <Select onValueChange={onSelectModel} value={selectedModel}>
                            <SelectTrigger className={cn('min-w-60', CONTROL_TEXT)}>
                              <SelectValue placeholder={m.model} />
                            </SelectTrigger>
                            <SelectContent>
                              {selectedModels.map(model => (
                                <SelectItem key={model} value={model}>
                                  {model}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <Button
                            disabled={!selectedProvider || !selectedModel || applying}
                            onClick={onApply}
                            size="sm"
                          >
                            {applying ? m.applying : t.common.apply}
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <p className="py-5 text-center text-xs text-muted-foreground">{m.noServices}</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={onAddService} size="sm" variant="textStrong">
          <Plus />
          {m.addService}
        </Button>
        <Button onClick={onAddCustomService} size="sm" variant="text">
          {m.addCustomService}
        </Button>
      </div>

      <div className="mt-2">
        <Button onClick={() => setAdvancedOpen(open => !open)} size="sm" variant="text">
          <Settings2 />
          {advancedOpen ? m.advancedConnectionsHide : m.advancedConnections}
        </Button>
        {advancedOpen && (
          <div className="mt-1 flex flex-wrap gap-1">
            <Button onClick={() => onOpenConnectionView('accounts')} size="sm" variant="text">
              {t.settings.nav.providerAccounts}
            </Button>
            <Button onClick={() => onOpenConnectionView('keys')} size="sm" variant="text">
              <KeyRound />
              {t.settings.nav.providerApiKeys}
            </Button>
            <Button onClick={() => onOpenConnectionView('custom-endpoints')} size="sm" variant="text">
              {t.settings.nav.providerCustomEndpoints}
            </Button>
          </div>
        )}
      </div>
    </section>
  )
}
