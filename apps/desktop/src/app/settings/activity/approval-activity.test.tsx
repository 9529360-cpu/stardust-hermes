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
  outcome: 'approved-once',
  mode: 'manual',
  command_preview: 'git status'
}

const GRANT = {
  id: 'grant-1',
  action_kind: 'command_pattern',
  target: 'git status*',
  created_at: '2026-10-09T10:00:00Z'
}

/** A tiny in-memory backend: writes mutate it, reads return the current state. */
function fakeBackend(state: { grants: (typeof GRANT)[]; audit: (typeof AUDIT_ENTRY)[] }) {
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

describe('ApprovalActivity', () => {
  beforeEach(() => {
    requestGateway.mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('shows standing grants and recent decisions from the gateway', async () => {
    fakeBackend({ grants: [GRANT], audit: [AUDIT_ENTRY] })

    renderPanel()

    expect(await screen.findByText('git status*')).toBeTruthy()
    expect(screen.getByText('terminal · approved-once')).toBeTruthy()
    expect(screen.getByText('git status')).toBeTruthy()
  })

  it('revokes a grant by id after confirmation, then reloads the authoritative list', async () => {
    const state = { grants: [GRANT], audit: [AUDIT_ENTRY] }

    fakeBackend(state)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    const dialog = await screen.findByRole('dialog')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Revoke' }))

    await waitFor(() => expect(screen.getByText('No standing grants. Approvals ask again each time.')).toBeTruthy())
    expect(requestGateway).toHaveBeenCalledWith('approval.grants.revoke', { id: 'grant-1' })
    expect(screen.queryByText('git status*')).toBeNull()
  })

  it('restores a grant the backend refused to revoke and says it is still active', async () => {
    const state = { grants: [GRANT], audit: [] as (typeof AUDIT_ENTRY)[] }

    fakeBackend(state)
    requestGateway.mockImplementation(async (method: string, params: Record<string, unknown>) => {
      if (method === 'approval.grants.revoke') {
        expect(params).toEqual({ id: 'grant-1' })

        return { revoked: false }
      }

      return method === 'approval.audit' ? { entries: [] } : { grants: state.grants }
    })

    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Revoke' }))

    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText("Couldn't revoke the grant. It is still active.")).toBeTruthy()
    expect(screen.getByText('git status*')).toBeTruthy()
  })

  it('never lets an earlier load overwrite a newer one', async () => {
    const state = { grants: [GRANT], audit: [AUDIT_ENTRY] }
    let releaseStaleAudit: (value: { entries: (typeof AUDIT_ENTRY)[] }) => void = () => undefined
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
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Revoke' }))

    expect(await screen.findByText('terminal · denied')).toBeTruthy()

    // The stale first response arrives last and must not win.
    releaseStaleAudit({ entries: [{ ...AUDIT_ENTRY, outcome: 'approved-once' }] })
    await waitFor(() => expect(screen.getByText('terminal · denied')).toBeTruthy())
    expect(screen.queryByText('terminal · approved-once')).toBeNull()
  })

  it('shows an honest error with a way to retry', async () => {
    requestGateway.mockRejectedValueOnce(new Error('gateway down'))
    requestGateway.mockRejectedValueOnce(new Error('gateway down'))

    renderPanel()

    expect(await screen.findByText("Couldn't load approval activity.")).toBeTruthy()

    fakeBackend({ grants: [], audit: [] })
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('No approval decisions yet.')).toBeTruthy()
  })
})
