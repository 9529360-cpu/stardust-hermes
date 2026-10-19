/**
 * Where the transcript should land when the content above the viewport changes
 * height: a page of older turns prepended, or a parked offset that the
 * transcript has finally grown tall enough to hold. Measured as the distance
 * from the viewport's top edge to the bottom of the scroll range, so the added
 * height cannot invalidate it.
 */
export interface ScrollAnchor {
  fromBottom: number
  /**
   * The settle loop parked this offset because the transcript is not tall
   * enough to hold it yet, so it waits for a taller tree. Every other anchor
   * was measured for the single commit that follows it.
   */
  parked: boolean
}

export interface ScrollAnchorOutcome {
  /** The scrollTop to write, or null when this commit cannot honor the anchor. */
  scrollTop: number | null
  /** Whether the anchor stays armed for a later commit. */
  keep: boolean
}

/**
 * What a commit does with the pending anchor. A prepend anchor describes the
 * viewport at one instant; once the commit it was measured for has passed, the
 * reader and the layout have moved on. Replaying it at a later, unrelated commit
 * (a new turn starting, a streamed row) throws the view to a position nobody
 * chose, so only a parked offset may outlive a commit that cannot hold it.
 */
export function resolveScrollAnchor(anchor: ScrollAnchor, scrollHeight: number): ScrollAnchorOutcome {
  if (scrollHeight >= anchor.fromBottom) {
    return { keep: false, scrollTop: scrollHeight - anchor.fromBottom }
  }

  return { keep: anchor.parked, scrollTop: null }
}
