/**
 * CX5 unified add/connect dialog — one dialog for every connector shape:
 * no-credential, API-key (secret entered masked; the value rides write-only
 * paths and is stored via the CX3/CX2 secure-store path, never echoed), OAuth
 * (CX4 browser flow), and custom URL/command services. Opened only by the
 * flag-gated connector catalog page (connectors.unified_dialog, off by
 * default); the legacy Capabilities surface never mounts this.
 */

import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'

import { addMcpServer, getMcpCatalogEntry, installMcpCatalogEntry } from '@/api/mcp'
import { getActionStatus } from '@/api/system'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { type ProfileScope, profileScopeKey, testMcpServer } from '@/hermes'
import { useI18n } from '@/i18n'
import { completeMcpDesktopOAuth, McpOAuthCancelled } from '@/lib/mcp-dashboard-oauth'
import { probeCache, probeKey } from '@/lib/mcp-probe-cache'
import { getServers } from '@/lib/mcp-servers'
import { prettyName } from '@/lib/text'
import type { McpCatalogEntry } from '@/types/hermes'

import { useHermesConfigRecord } from '../hooks/use-config-record'

const CATALOG_INSTALL_POLL_MS = 2000

/** A row of read-only detail (发布者 / 所需权限 / 运行位置) in the dialog body. */
function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] items-start gap-2 py-1">
      <span className="pt-0.5 text-[0.68rem] text-(--ui-text-tertiary)">{label}</span>
      <span className="min-w-0 text-xs break-words text-foreground/85">{children}</span>
    </div>
  )
}

/** Wait out a background catalog install action (git clone + bootstrap); a
 *  non-zero exit is a real failure, surfaced instead of a false success. */
async function pollInstallAction(action: string, profile: ProfileScope | undefined, failedLabel: string) {
  for (;;) {
    const status = await getActionStatus(action, 1, profile)

    if (!status.running) {
      if (status.exit_code !== 0) {
        throw new Error(failedLabel)
      }

      return
    }

    await new Promise(resolve => setTimeout(resolve, CATALOG_INSTALL_POLL_MS))
  }
}

export interface ConnectorAddDialogProps {
  entry: McpCatalogEntry | null
  profile?: ProfileScope
  onClose: () => void
  onConnected: (name: string) => void
}

export function ConnectorAddDialog({ entry, profile, onClose, onConnected }: ConnectorAddDialogProps) {
  const { t } = useI18n()
  const c = t.connectors
  const { data: configRecord } = useHermesConfigRecord(profile)
  const existingNames = useMemo(() => new Set(Object.keys(getServers(configRecord ?? null))), [configRecord])
  // Latest config for the post-install probe-cache write; a ref because the
  // connect closure must see the record CURRENT at probe time, not at render.
  const configRecordRef = useRef(configRecord)
  configRecordRef.current = configRecord

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The write-only secret draft (API key / OAuth never needs one). Held in
  // state only while the dialog is open; cleared on every close path, never
  // rendered outside the password input, never put in a toast or error.
  const [secretValues, setSecretValues] = useState<Record<string, string>>({})
  const [plainValues, setPlainValues] = useState<Record<string, string>>({})
  const cancelRef = useRef<(() => void) | null>(null)

  // Detail (publisher/permissions/transport beyond the list row) for the open entry.
  const [detail, setDetail] = useState<McpCatalogEntry | null>(null)
  useEffect(() => {
    setDetail(null)

    if (!entry) {
      return
    }

    let alive = true
    getMcpCatalogEntry(entry.name, profile)
      .then(loaded => {
        if (alive) {
          setDetail(loaded)
        }
      })
      .catch(() => {
        if (alive) {
          setDetail(entry)
        }
      })

    return () => {
      alive = false
    }
  }, [entry, profile])

  const open = entry !== null

  // Secret hygiene: any close path wipes the drafts and stops an in-flight
  // OAuth poller.
  const close = () => {
    cancelRef.current?.()
    cancelRef.current = null
    setSecretValues({})
    setPlainValues({})
    setError(null)
    setBusy(false)
    onClose()
  }

  useEffect(() => () => cancelRef.current?.(), [])

  if (!entry) {
    return null
  }

  const isInstalled = existingNames.has(entry.name)
  const authType = entry.auth_type
  const runtime = detail?.runtime ?? (entry.transport === 'stdio' ? 'local' : 'remote')
  const secretEnv = (detail ?? entry).required_env.filter(envVar => envVar.secret !== false)
  const plainEnv = (detail ?? entry).required_env.filter(envVar => envVar.secret === false)

  const connect = async () => {
    if (busy) {
      return
    }

    setError(null)

    if (isInstalled) {
      setError(c.nameDuplicate(entry.name))

      return
    }

    if (secretEnv.some(envVar => envVar.required && !secretValues[envVar.name]?.trim())) {
      setError(c.apiKeyRequired)

      return
    }

    if (plainEnv.some(envVar => envVar.required && !plainValues[envVar.name]?.trim())) {
      setError(c.urlOrCommandRequired)

      return
    }

    setBusy(true)

    try {
      // Catalog entries all land through the catalog install path: it writes
      // the manifest's server block (env references / auth: oauth marker)
      // exactly like the CLI, git-bootstrap entries run as a polled action.
      const env: Record<string, string> = {}

      for (const envVar of plainEnv) {
        const value = plainValues[envVar.name]?.trim()

        if (value) {
          env[envVar.name] = value
        }
      }

      for (const envVar of secretEnv) {
        const value = secretValues[envVar.name]?.trim()

        if (value) {
          env[envVar.name] = value
        }
      }

      const res = await installMcpCatalogEntry(entry.name, env, profile)

      if (res.background && res.action) {
        await pollInstallAction(res.action, profile, c.installFailed(entry.name))
      }

      if (authType === 'oauth') {
        // CX4 browser flow on top of the installed `auth: oauth` server: the
        // system browser opens, tokens land in the secure store — this side
        // never sees them.
        await completeMcpDesktopOAuth({
          serverName: entry.name,
          profile,
          cancelled: () => false
        })
      }

      // Immediate feedback: handshake now, not "trust me". A failed probe is
      // still a connected config row — report it, the list will show state.
      const probed = await testMcpServer(entry.name, profile).catch(() => null)

      // ConnectedRow reads probeCache; without this write a fresh install
      // stays 未连接 until a manual recheck. Key on the just-written server
      // block so the row's fingerprint lookup hits.
      if (probed) {
        const installed = getServers(configRecordRef.current ?? null)[entry.name]
        probeCache.set(probeKey(entry.name, installed, profileScopeKey(profile ?? null)), {
          at: Date.now(),
          result: probed
        })
      }

      onConnected(entry.name)

      if (!probed?.ok) {
        setError(probed?.error ?? c.connectFailed(entry.name))
        setBusy(false)

        return
      }

      close()
    } catch (err) {
      if (!(err instanceof McpOAuthCancelled)) {
        setError(err instanceof Error ? err.message : String(err))
      }

      setBusy(false)
    }
  }

  const connectLabel = authType === 'oauth' ? c.oauthLogin : c.connect

  return (
    <Dialog onOpenChange={openNow => !openNow && close()} open={open}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{`${c.dialogTitle} · ${prettyName(entry.name)}`}</DialogTitle>
          <DialogDescription className="line-clamp-2">{entry.description}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-1 px-1">
          <DetailRow label={c.publisher}>{detail?.publisher || entry.source || '—'}</DetailRow>
          <DetailRow label={c.runtimeLocation}>
            {runtime === 'local' ? c.runtimeLocal : c.runtimeRemote}
            {detail?.transport === 'stdio' && detail?.command ? ` · ${detail.command}` : null}
            {detail?.transport === 'http' && detail?.url ? ` · ${detail.url}` : null}
          </DetailRow>
          {(detail?.permissions?.length ?? 0) > 0 && (
            <DetailRow label={c.requiredPermissions}>
              <ul className="list-disc pl-4">
                {detail?.permissions?.map(permission => (
                  <li key={permission}>{permission}</li>
                ))}
              </ul>
            </DetailRow>
          )}
          {isInstalled && <DetailRow label={c.connectedList}>{c.nameDuplicate(entry.name)}</DetailRow>}
        </div>

        {plainEnv.map(envVar => (
          <label className="grid gap-1.5 px-1 pt-2" key={envVar.name}>
            <span className="text-xs text-(--ui-text-secondary)">{envVar.prompt || envVar.name}</span>
            <Input
              data-slot={`connector-env-${envVar.name}`}
              onChange={event => setPlainValues(current => ({ ...current, [envVar.name]: event.target.value }))}
              value={plainValues[envVar.name] ?? ''}
            />
          </label>
        ))}
        {secretEnv.map(envVar => (
          <label className="grid gap-1.5 px-1 pt-2" key={envVar.name}>
            <span className="text-xs text-(--ui-text-secondary)">{envVar.prompt || envVar.name}</span>
            <Input
              autoComplete="off"
              data-slot={`connector-secret-${envVar.name}`}
              onChange={event => setSecretValues(current => ({ ...current, [envVar.name]: event.target.value }))}
              type="password"
              value={secretValues[envVar.name] ?? ''}
            />
          </label>
        ))}

        {error && (
          <p
            className="rounded-md bg-(--ui-bg-tertiary) px-3 py-2 text-xs text-(--ui-text-primary)"
            data-slot="connector-add-error"
          >
            {error}
          </p>
        )}

        <DialogFooter>
          <Button disabled={busy} onClick={close} variant="ghost">
            {c.dialogCancel}
          </Button>
          <Button data-slot="connector-connect" disabled={busy || isInstalled} onClick={() => void connect()}>
            {busy ? c.probing : connectLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** The 自定义服务（URL/命令） branch of the unified dialog: add an arbitrary
 *  HTTP or stdio server by URL or command. Same secret rules — a Bearer API
 *  key rides the CX3 bearer_token path and is never echoed. */
export interface CustomConnectorAddDialogProps {
  open: boolean
  profile?: ProfileScope
  onClose: () => void
  onConnected: (name: string) => void
}

export function CustomConnectorAddDialog({ open, profile, onClose, onConnected }: CustomConnectorAddDialogProps) {
  const { t } = useI18n()
  const c = t.connectors
  const { data: configRecord } = useHermesConfigRecord(profile)
  const existingNames = useMemo(() => new Set(Object.keys(getServers(configRecord ?? null))), [configRecord])

  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [command, setCommand] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [useApiKey, setUseApiKey] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    setName('')
    setUrl('')
    setCommand('')
    setApiKey('')
    setUseApiKey(false)
    setError(null)
    setBusy(false)
    onClose()
  }

  if (!open) {
    return null
  }

  const submit = async () => {
    if (busy) {
      return
    }

    setError(null)

    const trimmedName = name.trim()

    if (!trimmedName) {
      setError(c.nameRequired)

      return
    }

    if (existingNames.has(trimmedName)) {
      setError(c.nameDuplicate(trimmedName))

      return
    }

    const trimmedUrl = url.trim()
    const trimmedCommand = command.trim()

    if (!trimmedUrl && !trimmedCommand) {
      setError(c.urlOrCommandRequired)

      return
    }

    if (!trimmedUrl && useApiKey) {
      setError(c.commandRequired)

      return
    }

    if (useApiKey && !apiKey.trim()) {
      setError(c.apiKeyRequired)

      return
    }

    setBusy(true)

    try {
      // CX3 safe path: the key rides write-only bearer_token; the backend
      // persists it to the CX2 encrypted store (flag on) or .env, and config
      // keeps only a reference. It is never in the response.
      await addMcpServer(
        {
          name: trimmedName,
          url: trimmedUrl || undefined,
          command: trimmedCommand || undefined,
          auth: useApiKey ? 'header' : 'none',
          bearer_token: useApiKey ? apiKey.trim() : undefined
        },
        profile
      )
      onConnected(trimmedName)
      close()
    } catch (err) {
      // Backend errors are value-free (CX3 contract); surface as-is.
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog onOpenChange={openNow => !openNow && close()} open={open}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{c.customService}</DialogTitle>
          <DialogDescription>{c.dialogTitle}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 px-1">
          <label className="grid gap-1.5">
            <span className="text-xs text-(--ui-text-secondary)">{c.nameLabel}</span>
            <Input data-slot="custom-name" onChange={event => setName(event.target.value)} value={name} />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-(--ui-text-secondary)">{c.customUrlLabel}</span>
            <Input
              data-slot="custom-url"
              onChange={event => setUrl(event.target.value)}
              placeholder="https://example.com/mcp"
              value={url}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs text-(--ui-text-secondary)">{c.customCommandLabel}</span>
            <Input
              data-slot="custom-command"
              disabled={Boolean(url.trim())}
              onChange={event => setCommand(event.target.value)}
              placeholder="npx -y @scope/server"
              value={command}
            />
          </label>
          <label className="flex items-center gap-2 pt-1">
            <input
              checked={useApiKey}
              data-slot="custom-use-api-key"
              disabled={Boolean(command.trim())}
              onChange={event => setUseApiKey(event.target.checked)}
              type="checkbox"
            />
            <span className="text-xs text-(--ui-text-secondary)">{c.apiKeyLabel}</span>
          </label>
          {useApiKey && (
            <label className="grid gap-1.5">
              <span className="text-xs text-(--ui-text-secondary)">{c.apiKeyLabel}</span>
              <Input
                autoComplete="off"
                data-slot="custom-api-key"
                onChange={event => setApiKey(event.target.value)}
                type="password"
                value={apiKey}
              />
            </label>
          )}
        </div>

        {error && (
          <p
            className="rounded-md bg-(--ui-bg-tertiary) px-3 py-2 text-xs text-(--ui-text-primary)"
            data-slot="connector-add-error"
          >
            {error}
          </p>
        )}

        <DialogFooter>
          <Button disabled={busy} onClick={close} variant="ghost">
            {c.dialogCancel}
          </Button>
          <Button data-slot="custom-connect" disabled={busy} onClick={() => void submit()}>
            {busy ? c.probing : c.connect}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
