import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ProjectMenu } from './project-menu'
import type { SidebarProjectTree } from './workspace-groups'

afterEach(cleanup)

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { cancel: 'Cancel', confirm: 'Confirm', done: 'Done', loading: 'Loading…' },
      sidebar: {
        projects: {
          copyPath: 'Copy path',
          deleteConfirm: 'This cannot be undone.',
          menu: 'Project actions',
          menuAddFolder: 'Add folder',
          menuAppearance: 'Appearance',
          menuDelete: 'Delete',
          menuRename: 'Rename',
          menuSetActive: 'Set active',
          noColor: 'No color',
          removeFromSidebar: 'Remove from sidebar',
          reveal: 'Reveal in file manager'
        }
      }
    }
  })
}))

vi.mock('@/store/layout', () => ({
  $panesFlipped: {
    get: () => false,
    listen: () => () => {},
    subscribe: (fn: (v: boolean) => void) => {
      fn(false)

      return () => {}
    }
  },
  dismissAutoProject: vi.fn()
}))

vi.mock('@/store/projects', () => ({
  copyPath: vi.fn(),
  deleteProject: vi.fn(),
  openProjectAddFolder: vi.fn(),
  openProjectRename: vi.fn(),
  revealPath: vi.fn(),
  setActiveProject: vi.fn(),
  setProjectAppearance: vi.fn().mockResolvedValue(false)
}))

const project = {
  color: null,
  icon: null,
  id: 'p1',
  isAuto: false,
  label: 'Test D',
  path: '/repo'
} as unknown as SidebarProjectTree

const tipTrigger = (el: HTMLElement) => el.closest('[data-slot="tooltip-trigger"]')

const openTriggerMenu = (trigger: HTMLElement) => {
  // Radix's dropdown trigger opens on pointerdown (a synthetic 'click' fireEvent
  // alone won't do it), so fire the full mouse sequence a real click produces —
  // same technique as session-actions-menu.test.tsx (#67500).
  fireEvent.pointerDown(trigger, { button: 0, pointerType: 'mouse' })
  fireEvent.pointerUp(trigger, { button: 0, pointerType: 'mouse' })
  fireEvent.click(trigger)
}

describe('ProjectMenu', () => {
  it('keeps a directly accessible actions trigger and puts rename and delete in its menu', async () => {
    render(<ProjectMenu isActive={false} project={project} />)

    const button = screen.getByRole('button', { name: 'Project actions: Test D' })
    expect(tipTrigger(button)).toBeNull()
    expect(button.className).toContain('opacity-70')
    openTriggerMenu(button)
    expect(await screen.findByRole('menuitem', { name: 'Rename' })).toBeTruthy()
    expect(await screen.findByRole('menuitem', { name: 'Appearance' })).toBeTruthy()
    expect(await screen.findByRole('menuitem', { name: 'Reveal in file manager' })).toBeTruthy()
    expect(await screen.findByRole('menuitem', { name: 'Copy path' })).toBeTruthy()
    expect(await screen.findByRole('menuitem', { name: 'Set active' })).toBeTruthy()
    expect(await screen.findByRole('menuitem', { name: 'Delete…' })).toBeTruthy()
  })

  it('opens the appearance picker from the actions submenu', async () => {
    render(<ProjectMenu isActive={false} project={project} />)

    openTriggerMenu(screen.getByRole('button', { name: 'Project actions: Test D' }))

    const appearanceItem = await screen.findByRole('menuitem', { name: 'Appearance' })
    fireEvent.pointerMove(appearanceItem, { pointerType: 'mouse' })
    fireEvent.click(appearanceItem)

    expect(await screen.findByRole('button', { name: 'No color' })).toBeTruthy()
  })
})
