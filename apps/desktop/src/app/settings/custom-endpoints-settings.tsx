import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import {
  activateCustomEndpoint,
  deleteCustomEndpoint,
  getCustomEndpoints,
  saveCustomEndpoint,
  validateCustomEndpoint
} from '@/hermes'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { Check, Globe, Loader2, Plus, Save, Trash2, Zap } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { confirm } from '@/store/confirm'
import { notify, notifyError } from '@/store/notifications'
import type { CustomEndpoint, CustomEndpointUpdate } from '@/types/hermes'

import { useOnProfileSwitch } from '../hooks/use-on-profile-switch'\n\nimport { EmptyState, Pill, SectionHeading, SettingsContent, SettingsSkeleton } from './primitives'

interface CustomEndpointsSettingsProps {
  onConfigSaved?: () => void
  scopeProfile?: string
  onMainModelChanged?: (provider: string, model: string) => void
}

interface EndpointForm {
  apiKey: string
  baseUrl: string
  contextLength: string
  discoverModels: boolean
  id: string
  makeDefault: boolean
  model: string
  name: string
}

export function isEndpointUrl(value: string): boolean {
  try {
    const url = new URL(value.trim())

    return (url.protocol === 'http:' || url.protocol === 'https:') && Boolean(url.hostname)
  } catch {
    return false
  }
}

const EMPTY_FORM: EndpointForm = {
  apiKey: '',
  baseUrl: '',
  contextLength: '',
  discoverModels: true,
  id: '',
  makeDefault: true,
  model: '',
  name: ''
}

function formFromEndpoint(endpoint: CustomEndpoint): EndpointForm {
  return {
    apiKey: '',
    baseUrl: endpoint.base_url,
    contextLength: endpoint.context_length ? String(endpoint.context_length) : '',
    discoverModels: endpoint.discover_models,
    id: endpoint.id,
    makeDefault: Boolean(endpoint.is_current),
    model: endpoint.model,
    name: endpoint.name
  }
}

function toPayload(form: EndpointForm, models?: string[]): CustomEndpointUpdate {
  const contextLength = Number.parseInt(form.contextLength, 10)

  return {
    id: form.id.trim() || undefined,
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    model: form.model.trim(),
    api_key: form.apiKey.trim() || undefined,
    context_length: Number.isFinite(contextLength) && contextLength > 0 ? contextLength : undefined,
    discover_models: form.discoverModels,
    make_default: form.makeDefault,
    models: models?.length ? models : undefined
  }
}

export function CustomEndpointsSettings({
  onConfigSaved,
  onMainModelChanged,
  scopeProfile
}: CustomEndpointsSettingsProps) {
  const { t } = useI18n()
  const c = t.settings.customEndpoints
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [activating, setActivating] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [endpoints, setEndpoints] = useState<CustomEndpoint[]>([])
  const [form, setForm] = useState<EndpointForm>(EMPTY_FORM)
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const probeRevision = useRef(0)
  const [probeMessage, setProbeMessage] = useState<string | null>(null)

  function updateForm(update: (current: EndpointForm) => EndpointForm) {
    probeRevision.current++
    setProbeMessage(null)
    setForm(current => {
      const next = update(current)

      if (next.baseUrl !== current.baseUrl || next.apiKey !== current.apiKey || next.id !== current.id) {
        setDiscoveredModels([])
      }

      return next
    })
  }

  const loadProfile = useCallback(\n    async (epoch: number) => {\n      try {\n        const data = await getCustomEndpoints(scopeProfile)\n\n        if (profileEpoch.current !== epoch) {\n          return\n        }\n\n        setEndpoints(data.endpoints)\n        const current = data.endpoints.find(endpoint => endpoint.is_current) ?? data.endpoints[0]\n\n        if (current) {\n          setForm(formFromEndpoint(current))\n          setDiscoveredModels(current.models)\n        } else {\n          setForm(EMPTY_FORM)\n          setDiscoveredModels([])\n        }\n      } catch (err) {\n        if (profileEpoch.current === epoch) {\n          notifyError(err, c.loadFailed)\n        }\n      } finally {\n        if (profileEpoch.current === epoch) {\n          setLoading(false)\n        }\n      }\n    },\n    [c.loadFailed, scopeProfile]\n  )\n\n  const beginProfileReload = useCallback(() => {\n    const epoch = ++profileEpoch.current\n    probeRevision.current++\n    setLoading(true)\n    setSaving(false)\n    setTesting(false)\n    setActivating(null)\n    setDeleting(null)\n    setEndpoints([])\n    setForm(EMPTY_FORM)\n    setDiscoveredModels([])\n    setAdvancedOpen(false)\n    setProbeMessage(null)\n    void loadProfile(epoch)\n  }, [loadProfile])\n\n  async function refresh() {\n    const epoch = profileEpoch.current\n    const data = await getCustomEndpoints(scopeProfile)\n\n    if (profileEpoch.current === epoch) {\n      setEndpoints(data.endpoints)\n    }\n  }\n\n  useEffect(() => {\n    beginProfileReload()\n\n    return () => {\n      profileEpoch.current++\n      probeRevision.current++\n    }\n  }, [beginProfileReload])\n\n  // A live active-profile swap does not change scopeProfile when this panel is\n  // following the foreground profile (undefined). Explicit Applies-to targets\n  // stay pinned and are reloaded by the scopeProfile effect when that target changes.\n  useOnProfileSwitch(() => {\n    if (scopeProfile === undefined) {\n      beginProfileReload()\n    }\n  })\n
  async function handleSave() {
    if (!isEndpointUrl(form.baseUrl)) {
      setProbeMessage(c.validationFailed)

      return
    }

    const epoch = profileEpoch.current

    try {
      setSaving(true)
      const response = await saveCustomEndpoint(toPayload(form, discoveredModels), scopeProfile)

      if (profileEpoch.current !== epoch) {
        return
      }

      setEndpoints(response.endpoints)
      const saved = response.endpoints.find(endpoint => endpoint.id === response.id)

      if (saved) {
        setForm(formFromEndpoint(saved))
        setDiscoveredModels(saved.models)
      }

      if (saved && saved.is_current && scopeProfile === undefined) {
        onMainModelChanged?.(saved.id, saved.model)
      }

      triggerHaptic('success')
      onConfigSaved?.()
      notify({ kind: 'success', message: c.saved })
    } catch (err) {
      if (profileEpoch.current === epoch) {
        notifyError(err, c.saveFailed)
      }
    } finally {
      if (profileEpoch.current === epoch) {
        setSaving(false)
      }
    }
  }

  async function handleValidate() {
    if (!isEndpointUrl(form.baseUrl)) {
      setProbeMessage(c.validationFailed)

      return
    }

    const revision = ++probeRevision.current
    const epoch = profileEpoch.current

    try {
      setTesting(true)
      setProbeMessage(null)
      const response = await validateCustomEndpoint(toPayload(form), scopeProfile)

      if (revision !== probeRevision.current || profileEpoch.current !== epoch) {return}
      setDiscoveredModels(response.ok ? response.models : [])

      if (response.ok) {
        notify({
          kind: 'success',
          message: response.models.length ? c.validationReachableModels(response.models.length) : c.validationReachable
        })
      } else {
        setProbeMessage(response.message || c.validationFailed)
        notify({
          kind: response.reachable ? 'warning' : 'error',
          message: response.message ? `${c.validationFailed}: ${response.message}` : c.validationFailed
        })
      }
    } catch (err) {
      if (revision === probeRevision.current && profileEpoch.current === epoch) {notifyError(err, c.validationFailed)}
    } finally {
      if (revision === probeRevision.current && profileEpoch.current === epoch) {setTesting(false)}
    }
  }

  async function handleActivate(endpoint: CustomEndpoint) {
    const epoch = profileEpoch.current

    try {
      setActivating(endpoint.id)
      const response = await activateCustomEndpoint(endpoint.id, scopeProfile)

      if (profileEpoch.current !== epoch) {
        return
      }

      await refresh()

      if (profileEpoch.current !== epoch) {
        return
      }

      onConfigSaved?.()

      if (scopeProfile === undefined) {onMainModelChanged?.(response.provider, response.model)}
      triggerHaptic('success')
    } catch (err) {
      if (profileEpoch.current === epoch) {
        notifyError(err, c.activationFailed)
      }
    } finally {
      if (profileEpoch.current === epoch) {
        setActivating(null)
      }
    }
  }

  async function handleDelete(endpoint: CustomEndpoint) {
    if (!(await confirm({ destructive: true, title: c.deleteConfirm(endpoint.name) }))) {
      return
    }

    const epoch = profileEpoch.current

    try {
      setDeleting(endpoint.id)
      const response = await deleteCustomEndpoint(endpoint.id, scopeProfile)

      if (profileEpoch.current !== epoch) {
        return
      }

      setEndpoints(response.endpoints)

      if (form.id === endpoint.id) {
        setForm(EMPTY_FORM)
        setDiscoveredModels([])
      }

      onConfigSaved?.()
      triggerHaptic('success')
    } catch (err) {
      if (profileEpoch.current === epoch) {
        notifyError(err, c.deleteFailed)
      }
    } finally {
      if (profileEpoch.current === epoch) {
        setDeleting(null)
      }
    }
  }

  if (loading) {
    return <SettingsSkeleton sections={[{ heading: true, rows: 3 }]} />
  }

  const allModelOptions = Array.from(new Set([...discoveredModels, form.model].filter(Boolean)))
  const canSave = form.name.trim() && isEndpointUrl(form.baseUrl) && form.model.trim()

  return (
    <SettingsContent>
      <div className="space-y-6">
        <section>
          <SectionHeading icon={Globe} meta={`${endpoints.length}`} title={c.title} />
          <div className="divide-y divide-border/40 rounded-md border border-border/50">
            {endpoints.length ? (
              endpoints.map(endpoint => (
                <div className="grid gap-3 p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center" key={endpoint.id}>
                  <button
                    className="min-w-0 text-left"
                    onClick={() => {
                      probeRevision.current++
                      setProbeMessage(null)
                      setForm(formFromEndpoint(endpoint))
                      setDiscoveredModels(endpoint.models)
                      setAdvancedOpen(false)
                    }}
                    type="button"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-medium">{endpoint.name}</span>
                      {endpoint.is_current && (
                        <Pill tone="primary">
                          <Check className="size-3" />
                          {c.active}
                        </Pill>
                      )}
                      {endpoint.source === 'direct-config' && <Pill>config.yaml</Pill>}
                    </div>
                    <div className="mt-1 truncate font-mono text-[0.7rem] text-muted-foreground">
                      {endpoint.base_url}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>{endpoint.model}</span>
                      {endpoint.has_api_key && <span>{endpoint.api_key_preview ?? c.apiKeySet}</span>}
                    </div>
                  </button>
                  <div className="flex items-center gap-2 sm:justify-end">
                    <Button
                      disabled={endpoint.is_current || activating === endpoint.id}
                      onClick={() => void handleActivate(endpoint)}
                      size="sm"
                      variant="outline"
                    >
                      {activating === endpoint.id ? <Loader2 className="animate-spin" /> : <Zap />}
                      {activating === endpoint.id ? c.using : c.use}
                    </Button>
                    {endpoint.source !== 'direct-config' && (
                      <Button
                        className="hover:text-destructive"
                        disabled={deleting === endpoint.id}
                        onClick={() => void handleDelete(endpoint)}
                        size="icon-sm"
                        title={c.deleteEndpoint}
                        variant="ghost"
                      >
                        {deleting === endpoint.id ? <Loader2 className="animate-spin" /> : <Trash2 />}
                      </Button>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <EmptyState description={c.emptyDescription} title={c.emptyTitle} />
            )}
          </div>
        </section>

        <section>
          <SectionHeading icon={Plus} title={form.id ? c.editTitle : c.addTitle} />
          <div className="grid gap-3 rounded-md border border-border/50 p-3">
            <label className="grid gap-1.5 text-xs text-muted-foreground">
              {c.nameLabel}
              <Input
                onChange={event => updateForm(current => ({ ...current, name: event.target.value }))}
                placeholder={c.namePlaceholder}
                value={form.name}
              />
            </label>
            <label className="grid gap-1.5 text-xs text-muted-foreground">
              {c.endpointUrlLabel}
              <Input
                onChange={event => updateForm(current => ({ ...current, baseUrl: event.target.value }))}
                placeholder="http://127.0.0.1:8081/v1"
                value={form.baseUrl}
              />
            </label>
            <label className="grid gap-1.5 text-xs text-muted-foreground">
              {c.modelLabel}
              <Input
                list="custom-endpoint-models"
                onChange={event => updateForm(current => ({ ...current, model: event.target.value }))}
                placeholder="gpt-5.4"
                value={form.model}
              />
              <datalist id="custom-endpoint-models">
                {allModelOptions.map(model => (
                  <option key={model} value={model} />
                ))}
              </datalist>
            </label>
            {discoveredModels.length > 0 && (
              <div className="grid gap-1 text-xs text-muted-foreground">
                <p>{c.validationReachableModels(discoveredModels.length)}</p>
                <div className="flex flex-wrap gap-1">
                  {discoveredModels.map(model => (
                    <Button
                      key={model}
                      onClick={() => updateForm(current => ({ ...current, model }))}
                      size="sm"
                      variant={form.model === model ? 'outline' : 'ghost'}
                    >
                      {model}
                    </Button>
                  ))}
                </div>
              </div>
            )}
            <label className="grid gap-1.5 text-xs text-muted-foreground">
              {c.apiKeyLabel}
              <Input
                onChange={event => updateForm(current => ({ ...current, apiKey: event.target.value }))}
                placeholder={form.id ? c.apiKeyKeepExisting : c.apiKeyOptional}
                type="password"
                value={form.apiKey}
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Checkbox
                checked={form.makeDefault}
                onCheckedChange={checked => setForm(current => ({ ...current, makeDefault: checked === true }))}
              />
              {c.makeDefault}
            </label>

            <div>
              <Button onClick={() => setAdvancedOpen(open => !open)} size="sm" type="button" variant="ghost">
                {advancedOpen ? c.advancedHide : c.advanced}
              </Button>
            </div>

            {advancedOpen && (
              <div className="grid gap-3 rounded-md border border-border/40 bg-muted/20 p-3">
                <label className="grid gap-1.5 text-xs text-muted-foreground">
                  {c.providerIdLabel}
                  <Input
                    onChange={event => updateForm(current => ({ ...current, id: event.target.value }))}
                    placeholder={c.providerIdPlaceholder}
                    value={form.id}
                  />
                  <span className="text-[0.7rem] text-muted-foreground">{c.providerIdHint}</span>
                </label>
                <label className="grid gap-1.5 text-xs text-muted-foreground">
                  {c.contextLabel}
                  <Input
                    inputMode="numeric"
                    onChange={event => updateForm(current => ({ ...current, contextLength: event.target.value }))}
                    placeholder={c.contextPlaceholder}
                    value={form.contextLength}
                  />
                </label>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Checkbox
                    checked={form.discoverModels}
                    onCheckedChange={checked => setForm(current => ({ ...current, discoverModels: checked === true }))}
                  />
                  {c.discoverModels}
                </label>
              </div>
            )}

            {probeMessage && (
              <p className="text-xs text-destructive" role="alert">
                {probeMessage}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={testing || !isEndpointUrl(form.baseUrl)}
                onClick={() => void handleValidate()}
                variant="outline"
              >
                {testing ? <Loader2 className="animate-spin" /> : <Zap />}
                {testing ? c.testing : c.test}
              </Button>
              <Button disabled={saving || !canSave} onClick={() => void handleSave()}>
                {saving ? <Loader2 className="animate-spin" /> : <Save />}
                {saving ? c.saving : c.save}
              </Button>
              <Button
                className={cn(!form.id && 'hidden')}
                onClick={() => {
                  setForm(EMPTY_FORM)
                  setDiscoveredModels([])
                  setAdvancedOpen(false)
                }}
                type="button"
                variant="ghost"
              >
                {c.newEndpoint}
              </Button>
            </div>
          </div>
        </section>
      </div>
    </SettingsContent>
  )
}
