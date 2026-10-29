import { beforeEach, describe, expect, it } from 'vitest'

import { $fileBrowserOpen, $revealInTreeRequest, revealFileInTree, setFileBrowserOpen } from './layout'
import { $rightContextOpen } from './right-context'

beforeEach(() => {
  window.localStorage.clear()
  $revealInTreeRequest.set(null)
  $rightContextOpen.set(false)
  setFileBrowserOpen(false)
})

describe('revealFileInTree', () => {
  it('opens the contextual rail and the Files pane, then hands its path to the tree', () => {
    const target = 'D:/work/project/src/main.ts'

    revealFileInTree(target)

    expect($rightContextOpen.get()).toBe(true)
    expect($fileBrowserOpen.get()).toBe(true)
    expect($revealInTreeRequest.get()).toBe(target)
  })
})
