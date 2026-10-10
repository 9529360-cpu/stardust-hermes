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
import { confirm } from '@/store/confirm'
import { notify, notifyError } from '@/store/notifications'
import type { CustomEndpoint, CustomEndpointsResponse, CustomEndpointUpdate } from '@/types/hermes'

import { useOnProfileSwitch } from '../hooks/use-on-profile-switch'

import { EmptyState, ListRow, Pill, SectionHeading, SettingsContent, SettingsSkeleton } from './primitives'

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

    return (
      (url.protocol === 'http:' || url.protocol === 'https:') &&
      Boolean(url.hostname) &&
      !url.username &&
      !url.password
    )
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
  makeDefault: false,
  model: '',
  name: ''
}

// The Electron REST bridge reports HTTP failures as `Error("<status>: <body>")`.
function isConflictError(error: unknown): boolean {
  return /(^|\s)409:/.test(error instanceof Error ? error.message : String(error))
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
  // Whether this profile already has a main model. Adding a service never
  // replaces a working default implicitly, but on a profile with none (first
  // run) the first service must become the default or chat still has no model.
  const [hasMainModel, setHasMainModel] = useState(true)
  const [editorMode, setEditorMode] = useState<'add' | 'edit' | null>(null)
  const [form, setForm] = useState<EndpointForm>(EMPTY_FORM)
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const probeRevision = useRef(0)
  const profileEpoch = useRef(0)
  const [probeMessage, setProbeMessage] = useState<string | null>(null)

  function updateForm(update: (current: EndpointForm) => EndpointForm) {
    probeRevision.current++
    // An edit invalidates the in-flight probe; its stale finally must not leave
    // the Test control disabled until the panel is reopened.
    setTesting(false)
    setProbeMessage(null)
    setForm(current => {
      const next = update(current)

      if (next.baseUrl !== current.baseUrl || next.apiKey !== current.apiKey || next.id !== current.id) {
        setDiscoveredModels([])
      }

      return next
    })
  }

  const applyEndpoints = useCallback((data: CustomEndpointsResponse) => {
    setEndpoints(data.endpoints)
    setHasMainModel(Boolean(data.current?.model?.trim()) || data.endpoints.some(endpoint => endpoint.is_current))
  }, [])

  const loadProfile = useCallback(
    async (epoch: number) => {
      try {
        const data = await getCustomEndpoints(scopeProfile)

        if (profileEpoch.current !== epoch) {
          return
        }

        applyEndpoints(data)
      } catch (err) {
        if (profileEpoch.current === epoch) {
          notifyError(err, c.loadFailed)
        }
      } finally {
        if (profileEpoch.current === epoch) {
          setLoading(false)
        }
      }
    },
    [applyEndpoints, c.loadFailed, scopeProfile]
  )

  const beginProfileReload = useCallback(() => {
    const epoch = ++profileEpoch.current
    probeRevision.current++
    setLoading(true)
    setSaving(false)
    setTesting(false)
    setActivating(null)
    setDeleting(null)
    setEndpoints([])
    setEditorMode(null)
    setForm(EMPTY_FORM)
    setDiscoveredModels([])
    setAdvancedOpen(false)
    setProbeMessage(null)
    void loadProfile(epoch)
  }, [loadProfile])

  async function refresh() {
    const epoch = profileEpoch.current
    const data = await getCustomEndpoints(scopeProfile)

    if (profileEpoch.current === epoch) {
      applyEndpoints(data)
    }
  }

  function closeEditor() {
    probeRevision.current++
    setEditorMode(null)
    setForm(EMPTY_FORM)
    setDiscoveredModels([])
    setAdvancedOpen(false)
    setProbeMessage(null)
  }

  function beginAdd() {
    probeRevision.current++
    setEditorMode('add')
    setForm({ ...EMPTY_FORM, makeDefault: !hasMainModel })
    setDiscoveredModels([])
    setAdvancedOpen(false)
    setProbeMessage(null)
  }

  function beginEdit(endpoint: CustomEndpoint) {
    probeRevision.current++
    setEditorMode('edit')
    setForm(formFromEndpoint(endpoint))
    setDiscoveredModels(endpoint.models)
    setAdvancedOpen(false)
    setProbeMessage(null)
  }

  useEffect(() => {
    beginProfileReload()

    return () => {
      profileEpoch.current++
      probeRevision.current++
    }
  }, [beginProfileReload])

  // A live active-profile swap does not change scopeProfile when this panel is
  // following the foreground profile (undefined). Explicit Applies-to targets
  // stay pinned and are reloaded by the scopeProfile effect when that target changes.
  useOnProfileSwitch(() => {
    if (scopeProfile === undefined) {
      beginProfileReload()
    }
  })

  async function handleSave() {
    if (!editorMode) {
      return
    }

    if (!isEndpointUrl(form.baseUrl)) {
      setProbeMessage(c.validationFailed)

      return
    }

    const epoch = profileEpoch.current

    try {
      setSaving(true)

      const response = await saveCustomEndpoint(
        { ...toPayload(form, discoveredModels), create_only: editorMode === 'add' },
        scopeProfile
      )

      if (profileEpoch.current !== epoch) {
        return
      }

      applyEndpoints(response)
      const saved = response.endpoints.find(endpoint => endpoint.id === response.id)

      if (saved && saved.is_current && scopeProfile === undefined) {
        onMainModelChanged?.(saved.id, saved.model)
      }

      closeEditor()
      triggerHaptic('success')
      onConfigSaved?.()
      notify({ kind: 'success', message: c.saved })
    } catch (err) {
      if (profileEpoch.current === epoch) {
        if (editorMode === 'add' && isConflictError(err)) {
          // Add is create-only: the backend refused to overwrite a saved
          // service with the same name. Say so where the user is typing.
          setProbeMessage(c.duplicateService)
        } else {
          notifyError(err, c.saveFailed)
        }
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

      applyEndpoints(response)

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

  if (editorMode) {
    return (
      <SettingsContent>
        <section>
          <SectionHeading
            aside={
              <Button disabled={saving} onClick={closeEditor} size="inline" type="button" variant="text">
                {t.common.cancel}
              </Button>
            }
            icon={editorMode === 'add' ? Plus : Globe}
            title={editorMode === 'add' ? c.addTitle : c.editTitle}
          />

          <div className="grid gap-3">
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
                placeholder={editorMode === 'edit' ? c.apiKeyKeepExisting : c.apiKeyOptional}
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
              <div className="grid gap-3 pt-1">
                <label className="grid gap-1.5 text-xs text-muted-foreground">
                  {c.providerIdLabel}
                  <Input
                    disabled={editorMode === 'edit'}
                    onChange={
                      editorMode === 'add'
                        ? event => updateForm(current => ({ ...current, id: event.target.value }))
                        : undefined
                    }
                    placeholder={c.providerIdPlaceholder}
                    value={form.id}
                  />
                  {editorMode === 'add' && (
                    <span className="text-[0.7rem] text-muted-foreground">{c.providerIdHint}</span>
                  )}
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
            </div>
          </div>
        </section>
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <section>
        <SectionHeading
          aside={
            <Button onClick={beginAdd} size="sm" type="button" variant="secondary">
              <Plus />
              {c.addTitle}
            </Button>
          }
          icon={Globe}
          meta={`${endpoints.length}`}
          title={c.title}
        />
        {endpoints.length ? (
          <div>
            {endpoints.map(endpoint => (
              <ListRow
                action={
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      disabled={endpoint.is_current || activating === endpoint.id}
                      onClick={() => void handleActivate(endpoint)}
                      size="sm"
                      variant="outline"
                    >
                      {activating === endpoint.id ? <Loader2 className="animate-spin" /> : <Zap />}
                      {activating === endpoint.id ? c.using : c.use}
                    </Button>
                    <Button onClick={() => beginEdit(endpoint)} size="inline" type="button" variant="textStrong">
                      {c.editTitle}
                    </Button>
                    {endpoint.source !== 'direct-config' && (
                      <Button
                        aria-label={c.deleteEndpoint}
                        className="hover:text-destructive"
                        disabled={deleting === endpoint.id}
                        onClick={() => void handleDelete(endpoint)}
                        size="icon-sm"
                        variant="ghost"
                      >
                        {deleting === endpoint.id ? <Loader2 className="animate-spin" /> : <Trash2 />}
                      </Button>
                    )}
                  </div>
                }
                description={
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span>{endpoint.model}</span>
                    {endpoint.has_api_key && <span>{endpoint.api_key_preview ?? c.apiKeySet}</span>}
                  </span>
                }
                hint={endpoint.base_url}
                key={endpoint.id}
                title={
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate">{endpoint.name}</span>
                    {endpoint.is_current && (
                      <Pill tone="primary">
                        <Check className="size-3" />
                        {c.active}
                      </Pill>
                    )}
                    {endpoint.source === 'direct-config' && <Pill>config.yaml</Pill>}
                  </span>
                }
              />
            ))}
          </div>
        ) : (
          <EmptyState description={c.emptyDescription} title={c.emptyTitle} />
        )}
      </section>
    </SettingsContent>
  )
}
