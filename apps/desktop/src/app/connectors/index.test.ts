import { describe, expect, it } from 'vitest'

import { connectorsUnifiedDialogEnabled } from './index'

describe('connectorsUnifiedDialogEnabled (CX5 flag read)', () => {
  it('is off without a config record (default OFF)', () => {
    expect(connectorsUnifiedDialogEnabled(undefined)).toBe(false)
    expect(connectorsUnifiedDialogEnabled(null)).toBe(false)
    expect(connectorsUnifiedDialogEnabled({})).toBe(false)
  })

  it('is off when the connectors section exists but the flag is absent/false', () => {
    expect(connectorsUnifiedDialogEnabled({ connectors: {} })).toBe(false)
    expect(connectorsUnifiedDialogEnabled({ connectors: { unified_dialog: false } })).toBe(false)
    expect(connectorsUnifiedDialogEnabled({ connectors: { oauth_secure_store: true } })).toBe(false)
  })

  it('reads only connectors.unified_dialog === true as on', () => {
    expect(connectorsUnifiedDialogEnabled({ connectors: { unified_dialog: true } })).toBe(true)
    // Truthy non-boolean values do not count — the flag is a real boolean.
    expect(connectorsUnifiedDialogEnabled({ connectors: { unified_dialog: 'true' } })).toBe(false)
    expect(connectorsUnifiedDialogEnabled({ connectors: { unified_dialog: 1 } })).toBe(false)
  })

  it('fails safe when the section is malformed', () => {
    expect(connectorsUnifiedDialogEnabled({ connectors: 'yes' })).toBe(false)
    expect(connectorsUnifiedDialogEnabled({ connectors: null })).toBe(false)
  })
})
