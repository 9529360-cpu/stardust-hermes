import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useState } from 'react'

import {
  type ActionItemSpec,
  ActionsContextMenu,
  ActionsMenu,
  type MenuKit,
  renderActionItem
} from '@/components/ui/actions-menu'
import { Codicon } from '@/components/ui/codicon'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useI18n } from '@/i18n'
import { $panesFlipped, dismissAutoProject } from '@/store/layout'
import {
  copyPath,
  deleteProject,
  openProjectAddFolder,
  openProjectRename,
  revealPath,
  setActiveProject,
  setProjectAppearance
} from '@/store/projects'

import { ProjectAppearancePicker } from './project-appearance'
import type { SidebarProjectTree } from './workspace-groups'

// Shared per-project state + handlers, so the kebab dropdown and the row's
// right-click menu drive the exact same actions. Modeled on git GUIs (GitHub
// Desktop / GitKraken): reveal in the file manager, copy path, and "Remove from
// sidebar" (never deletes files — auto projects are dismissed, explicit ones
// drop their entry). Explicit projects additionally get rename / add folder /
// set active.
function useProjectActions({
  project,
  isActive,
  scoped,
  onExitScope
}: {
  project: SidebarProjectTree
  isActive: boolean
  scoped: boolean
  onExitScope?: () => void
}) {
  const { t } = useI18n()
  const p = t.sidebar.projects
  const target = { id: project.id, name: project.label }
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)

  const removeAuto = () => {
    dismissAutoProject(project.id)

    if (scoped) {
      onExitScope?.()
    }
  }

  const confirmDelete = async () => {
    await deleteProject(project.id)

    if (scoped) {
      onExitScope?.()
    }
  }

  // Rename / add folder / set active — explicit projects only (auto ones lack a
  // materialized record).
  const identityItems: ActionItemSpec[] = project.isAuto
    ? []
    : [
        { icon: 'edit', key: 'rename', label: p.menuRename, onSelect: () => openProjectRename(target) },
        {
          icon: 'new-folder',
          key: 'add-folder',
          label: p.menuAddFolder,
          onSelect: () => openProjectAddFolder(target)
        },
        {
          disabled: isActive,
          icon: 'target',
          key: 'set-active',
          label: p.menuSetActive,
          onSelect: () => void setActiveProject(project.id)
        }
      ]

  const pathItems: ActionItemSpec[] = [
    {
      disabled: !project.path,
      icon: 'folder-opened',
      key: 'reveal',
      label: p.reveal,
      onSelect: () => void revealPath(project.path)
    },
    {
      disabled: !project.path,
      icon: 'copy',
      key: 'copy',
      label: p.copyPath,
      onSelect: () => void copyPath(project.path)
    }
  ]

  const dangerItem: ActionItemSpec = project.isAuto
    ? { icon: 'trash', key: 'remove', label: p.removeFromSidebar, onSelect: removeAuto, variant: 'destructive' }
    : {
        icon: 'trash',
        key: 'delete',
        label: `${p.menuDelete}…`,
        onSelect: () => setConfirmDeleteOpen(true),
        variant: 'destructive'
      }

  const confirmDialog = (
    <ConfirmDialog
      confirmLabel={p.menuDelete}
      description={p.deleteConfirm}
      destructive
      onClose={() => setConfirmDeleteOpen(false)}
      onConfirm={confirmDelete}
      open={confirmDeleteOpen}
      title={`${p.menuDelete} "${project.label}"?`}
    />
  )

  return { confirmDialog, dangerItem, identityItems, pathItems }
}

// Per-project actions shared by the visible kebab and row context menu. Hidden
// until the row is hovered, matching the + affordance.
export function ProjectMenu({
  project,
  isActive,
  scoped = false,
  onExitScope,
  anchorRef
}: {
  project: SidebarProjectTree
  isActive: boolean
  // True when rendered in the entered-project header, so removal can leave the
  // now-defunct scope.
  scoped?: boolean
  onExitScope?: () => void
  // Anchor the actions menu to the whole row when rendered inside the project
  // overview, so its submenu opens toward the content area. Other surfaces use
  // the kebab as the menu anchor.
  anchorRef?: React.RefObject<HTMLElement | null>
}) {
  const { t } = useI18n()
  const p = t.sidebar.projects
  const panesFlipped = useStore($panesFlipped)
  const [menuOpen, setMenuOpen] = useState(false)

  const { confirmDialog, dangerItem, identityItems, pathItems } = useProjectActions({
    isActive,
    onExitScope,
    project,
    scoped
  })

  const canTheme = !project.isAuto || Boolean(project.path)

  // Appearance lives inside the same menu as other project actions, avoiding a
  // second overlay that would close the menu when it receives focus.
  const appearanceItem = (kit: MenuKit) => (
    <kit.Sub key="appearance">
      <kit.SubTrigger>
        <Codicon name="symbol-color" size="0.875rem" />
        <span>{p.menuAppearance}</span>
      </kit.SubTrigger>
      <kit.SubContent className="w-auto p-2">
        <ProjectAppearancePicker
          color={project.color ?? null}
          icon={project.icon ?? null}
          noColorLabel={p.noColor}
          onColor={async color => {
            if (await setProjectAppearance(project, { color })) {
              setMenuOpen(false)
            }
          }}
          onIcon={async icon => {
            if (await setProjectAppearance(project, { icon })) {
              setMenuOpen(false)
            }
          }}
        />
      </kit.SubContent>
    </kit.Sub>
  )

  const items = (kit: MenuKit) => (
    <>
      {project.isAuto ? (
        project.path && (
          <>
            {appearanceItem(kit)}
            <kit.Separator />
          </>
        )
      ) : (
        <>
          {identityItems.slice(0, 1).map(item => renderActionItem(kit, item))}
          {appearanceItem(kit)}
          {identityItems.slice(1).map(item => renderActionItem(kit, item))}
          <kit.Separator />
        </>
      )}
      {pathItems.map(item => renderActionItem(kit, item))}
      <kit.Separator />
      {renderActionItem(kit, dangerItem)}
    </>
  )

  const triggerButton = (
    <button
      aria-label={`${p.menu}: ${project.label}`}
      className="grid size-5 shrink-0 place-items-center rounded-sm bg-transparent text-(--ui-text-tertiary) opacity-70 hover:bg-(--ui-control-hover-background) hover:text-foreground hover:opacity-100 focus-visible:opacity-100"
      onClick={event => event.stopPropagation()}
      type="button"
    >
      <Codicon name="kebab-vertical" size="0.75rem" />
    </button>
  )

  return (
    <>
      <ActionsMenu
        align={anchorRef ? 'start' : 'end'}
        ariaLabel={`${p.menu}: ${project.label}`}
        contentClassName="w-48"
        items={items}
        onCloseAutoFocus={event => event.preventDefault()}
        onOpenChange={setMenuOpen}
        open={menuOpen}
        side={anchorRef ? (panesFlipped ? 'left' : 'right') : undefined}
        sideOffset={6}
      >
        {triggerButton}
      </ActionsMenu>
      {confirmDialog}
    </>
  )
}

interface ProjectContextMenuProps {
  project: SidebarProjectTree
  isActive: boolean
  scoped?: boolean
  onExitScope?: () => void
  children: React.ReactNode
}

// Wrap a project row so right-clicking it opens the same actions as its kebab,
// including the Appearance submenu with the shared swatch + icon picker.
export function ProjectContextMenu({
  project,
  isActive,
  scoped = false,
  onExitScope,
  children
}: ProjectContextMenuProps) {
  const { t } = useI18n()
  const p = t.sidebar.projects
  const [menuOpen, setMenuOpen] = useState(false)

  const { confirmDialog, dangerItem, identityItems, pathItems } = useProjectActions({
    isActive,
    onExitScope,
    project,
    scoped
  })

  const canTheme = !project.isAuto || Boolean(project.path)

  const applyAppearance = async (patch: { color?: null | string; icon?: null | string }) => {
    if (await setProjectAppearance(project, patch)) {
      setMenuOpen(false)
    }
  }

  const items = (kit: MenuKit) => (
    <>
      {identityItems.map(item => renderActionItem(kit, item))}
      {canTheme && (
        <kit.Sub>
          <kit.SubTrigger>
            <Codicon name="symbol-color" size="0.875rem" />
            <span>{p.menuAppearance}</span>
          </kit.SubTrigger>
          <kit.SubContent className="w-auto p-2">
            <ProjectAppearancePicker
              color={project.color ?? null}
              icon={project.icon ?? null}
              noColorLabel={p.noColor}
              onColor={color => applyAppearance({ color })}
              onIcon={icon => applyAppearance({ icon })}
            />
          </kit.SubContent>
        </kit.Sub>
      )}
      {(identityItems.length > 0 || canTheme) && <kit.Separator />}
      {pathItems.map(item => renderActionItem(kit, item))}
      <kit.Separator />
      {renderActionItem(kit, dangerItem)}
    </>
  )

  return (
    <>
      <ActionsContextMenu
        ariaLabel={p.menu}
        contentClassName="w-48"
        items={items}
        onOpenChange={setMenuOpen}
        open={menuOpen}
      >
        {children}
      </ActionsContextMenu>
      {confirmDialog}
    </>
  )
}
