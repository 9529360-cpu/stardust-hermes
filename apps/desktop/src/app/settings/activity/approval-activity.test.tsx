import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { ApprovalActivity } from './approval-activity'

const requestGateway = vi.hoisted(() => vi.fn())

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

const AUDIT_ENTRY = {
  ts: '2026-10-10T08:00:00Z',
  session_key: 'session-1',
  kind: 'command',
  tool_name: 'terminal',
  description: 'run a command',
  pattern_key: 'git status',
  outcome: 'approved_once',
  mode: 'manual',
  command_preview: 'git status'
}

// The grant was allowed today, so its detail is today's short date (no year), whatever day the suite runs.
const NOW_ISO = new Date().toISOString()
const TODAY = new Date().toLocaleDateString('en', { day: 'numeric', month: 'short' })

const GRANT = {
  id: 'grant-1',
  action_kind: 'command_pattern',
  target: 'git status*',
  created_at: NOW_ISO
}

type Audit = (typeof AUDIT_ENTRY)[]
type Grants = (typeof GRANT)[]

/** A small in-memory backend: writes mutate it, reads return the current state. */
function fakeBackend(state: { grants: Grants; audit: Audit }) {
  requestGateway.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'approval.audit') {
      return { entries: state.audit }
    }

    if (method === 'approval.grants.list') {
      return { grants: state.grants }
    }

    if (method === 'approval.grants.revoke') {
      const before = state.grants.length
      state.grants = state.grants.filter(grant => grant.id !== params.id)

      return { revoked: state.grants.length < before }
    }

    throw new Error(`unexpected ${method}`)
  })
}

function renderPanel() {
  return render(
    <I18nProvider>
      <ApprovalActivity />
    </I18nProvider>
  )
}

function confirmRevoke() {
  return findDialogButton('Revoke')
}

async function findDialogButton(name: string) {
  const dialog = await screen.findByRole('dialog')

  return within(dialog).getByRole('button', { name })
}

describe('ApprovalActivity', () => {
  beforeEach(() => {
    requestGateway.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('states each permission in plain words and lists recent confirmations', async () => {
    fakeBackend({ grants: [GRANT], audit: [AUDIT_ENTRY] })

    renderPanel()

    expect(await screen.findByText('Run git status* without asking')).toBeTruthy()
    expect(screen.getByText(`Allowed ${TODAY}`)).toBeTruthy()
    expect(screen.getByText('Run git status')).toBeTruthy()
    expect(screen.getByText('Allowed once')).toBeTruthy()
  })

  it('revokes a permission after confirmation, then reloads the authoritative list', async () => {
    const state = { grants: [GRANT], audit: [AUDIT_ENTRY] }

    fakeBackend(state)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    fireEvent.click(await confirmRevoke())

    expect(await screen.findByText('No standing permissions yet. I ask before anything that needs your approval.')).toBeTruthy()
    expect(requestGateway).toHaveBeenCalledWith('approval.grants.revoke', { id: 'grant-1' })
    expect(screen.queryByText('Run git status* without asking')).toBeNull()
  })

  it('restores a permission the backend refused to revoke and says it is still allowed', async () => {
    const state = { grants: [GRANT], audit: [] as Audit }

    requestGateway.mockImplementation(async (method: string, params: Record<string, unknown>) => {
      if (method === 'approval.grants.revoke') {
        expect(params).toEqual({ id: 'grant-1' })

        return { revoked: false }
      }

      return method === 'approval.audit' ? { entries: state.audit } : { grants: state.grants }
    })

    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    fireEvent.click(await confirmRevoke())

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText("Couldn't revoke that. It's still allowed.")).toBeTruthy()
    expect(screen.getByText('Run git status* without asking')).toBeTruthy()
  })

  it('never lets an earlier load overwrite a newer one', async () => {
    const state = { grants: [GRANT], audit: [AUDIT_ENTRY] }
    let releaseStaleAudit: (value: { entries: Audit }) => void = () => undefined
    let auditCalls = 0

    requestGateway.mockImplementation(async (method: string) => {
      if (method === 'approval.audit') {
        auditCalls += 1

        if (auditCalls === 1) {
          // The first load's audit stays pending until the test releases it.
          return new Promise(resolve => {
            releaseStaleAudit = resolve
          })
        }

        return { entries: [{ ...AUDIT_ENTRY, outcome: 'denied' }] }
      }

      if (method === 'approval.grants.list') {
        return { grants: state.grants }
      }

      state.grants = []

      return { revoked: true }
    })

    renderPanel()

    // Revoke while the first load is still waiting on its audit read.
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    fireEvent.click(await confirmRevoke())

    expect(await screen.findByText('Declined')).toBeTruthy()

    // The stale first response arrives last and must not win.
    releaseStaleAudit({ entries: [{ ...AUDIT_ENTRY, outcome: 'approved_once' }] })
    await waitFor(() => expect(screen.getByText('Declined')).toBeTruthy())
    expect(screen.queryByText('Allowed once')).toBeNull()
  })

  it('shows an honest error with a way to retry', async () => {
    requestGateway.mockRejectedValueOnce(new Error('gateway down'))
    requestGateway.mockRejectedValueOnce(new Error('gateway down'))

    renderPanel()

    expect(await screen.findByText("Couldn't load your permissions.")).toBeTruthy()

    fakeBackend({ grants: [], audit: [] })
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Nothing has needed your confirmation yet.')).toBeTruthy()
  })
})
