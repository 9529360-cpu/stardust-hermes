import { describe, expect, it } from 'vitest'

import { resolveScrollAnchor } from './scroll-anchor'

describe('resolveScrollAnchor', () => {
  it('lands the anchor once the transcript can hold it and disarms it', () => {
    expect(resolveScrollAnchor({ fromBottom: 700, parked: false }, 5000)).toEqual({ keep: false, scrollTop: 4300 })
    expect(resolveScrollAnchor({ fromBottom: 700, parked: true }, 700)).toEqual({ keep: false, scrollTop: 0 })
  })

  it('drops a prepend anchor the commit could not honor instead of leaving it armed', () => {
    // Measured at 7104px, but the commit it was taken for found only 5805px:
    // left armed it would fire at the next commit that happens to be taller.
    expect(resolveScrollAnchor({ fromBottom: 7104, parked: false }, 5805)).toEqual({ keep: false, scrollTop: null })
  })

  it('keeps a parked offset armed until the transcript can hold it', () => {
    expect(resolveScrollAnchor({ fromBottom: 7104, parked: true }, 5805)).toEqual({ keep: true, scrollTop: null })
  })
})
