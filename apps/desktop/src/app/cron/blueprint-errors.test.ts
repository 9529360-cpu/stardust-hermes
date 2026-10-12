import { describe, expect, it } from 'vitest'

import { cleanBlueprintFieldError } from './blueprints'

describe('cleanBlueprintFieldError', () => {
  it('keeps only the backend message from an IPC-wrapped 422 with a JSON detail', () => {
    const wrapped =
      'Error invoking remote method \'stardust:api\': Error: 422: {"detail":"missing required value: folder (Which folder?)"}'

    expect(cleanBlueprintFieldError(wrapped)).toBe('missing required value: folder (Which folder?)')
  })

  it('drops the status code from a plain 422 message', () => {
    expect(cleanBlueprintFieldError("422: invalid time '25:99' — use HH:MM (24h)")).toBe(
      "invalid time '25:99' — use HH:MM (24h)"
    )
  })

  it('leaves a message it cannot read unchanged', () => {
    expect(cleanBlueprintFieldError('Network request failed')).toBe('Network request failed')
  })
})
