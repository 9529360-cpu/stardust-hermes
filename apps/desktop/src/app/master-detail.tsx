import './master-detail.css'

import { useStore } from '@nanostores/react'
import {
  Children,
  type CSSProperties,
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState
} from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { RowButton } from '@/components/ui/row-button'
import { Switch } from '@/components/ui/switch'
import { Tip } from '@/components/ui/tooltip'
import { usePointerResize } from '@/hooks/use-pointer-resize'
import { useScrollFade } from '@/hooks/use-scroll-fade'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import {
  $paneHeightOverride,
  $paneState,
  $paneWidthOverride,
  setPaneHeightOverride,
  setPaneWidthOverride
} from '@/store/panes'

// Monospace capability chip (tool name, transport, …). Shared by the Skills
// and MCP tabs so the pill reads identically everywhere.
export function ToolChip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span
      className="rounded-md bg-(--ui-bg-quinary) px-1.5 py-0.5 font-mono text-[0.65rem] text-(--ui-text-tertiary)"
      title={title}
    >
      {children}
    </span>
  )
}

// Master–detail page scaffolding (14rem rail, p-2, centered max-w-2xl detail):
// dense uniform rows on the left, roomy inspector on the right. Shared by the
// Capabilities and Messaging pages — pages bring their own row/detail content
// (CapRow here is the toggle-row flavor; Messaging has its own avatar rows).

// `pane` docks a full-bleed work surface (editor, log viewer, terminal) below
// the whole master–detail grid — the app's bottom-pane pattern, page-local.
// The wide-rail track shared by every Capabilities tab (skills/tools/mcp) so
// the three read as one page. Exported for pages that build their own grid
// (the MCP tab's cursor-driven layout) but must stay in step.
// `--md-split` is the drag override slot: unset it falls back to the declared
// track, so grids without a resize sash render exactly as before.
export const MASTER_DETAIL_WIDE_COLS = 'sm:grid-cols-[minmax(0,var(--md-split,0.75fr))_minmax(0,1fr)]'

// Column-seam drag clamps: the rail can't shrink below a readable row, the
// detail keeps enough room for its centered column.
const SPLIT_MIN_LEFT_PX = 180
const SPLIT_MIN_RIGHT_PX = 320

// `split="wide"` gives list-heavy pages a rail that shares the page with a
// sparse detail (skills/tools/mcp); the default 14rem rail suits pages whose
// detail carries the weight (messaging). A `resizeId` turns the column seam
// into a drag sash: the rail width persists in the pane store under that id
// (same store as the terminal/editor panes), double-click resets to default.
export function MasterDetail({
  children,
  pane,
  resizeId,
  split = 'rail'
}: {
  children: ReactNode
  pane?: ReactNode
  /** Pane-store key — when set, the seam between the two columns becomes a
   *  drag-resizable sash and the rail width persists under this id. */
  resizeId?: string
  split?: 'rail' | 'wide'
}) {
  const gridRef = useRef<HTMLDivElement>(null)
  // Unconditional hook (rules of hooks) — the '' atom is inert when no id.
  const override = useStore($paneWidthOverride(resizeId ?? ''))
  const [dragging, setDragging] = useState(false)
  const startResize = usePointerResize(resizeId)

  const startSplitDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const grid = gridRef.current

    if (!resizeId || !grid || event.button !== 0) {
      return
    }

    const startWidth = (grid.children[0] as HTMLElement).getBoundingClientRect().width
    const max = Math.max(SPLIT_MIN_LEFT_PX, grid.getBoundingClientRect().width - SPLIT_MIN_RIGHT_PX)
    const rtl = grid.ownerDocument.defaultView?.getComputedStyle(grid).direction === 'rtl'
    let previewWidth = Math.round(startWidth)
    const clearPreview = () => grid.style.removeProperty('--md-drag-split')

    startResize(event, {
      axis: 'x',
      onActiveChange: setDragging,
      onPreview: delta => {
        const logicalDelta = rtl ? -delta : delta
        previewWidth = Math.round(Math.min(max, Math.max(SPLIT_MIN_LEFT_PX, startWidth + logicalDelta)))
        grid.style.setProperty('--md-drag-split', `${previewWidth}px`)
      },
      onCommit: () => {
        try {
          if (previewWidth !== Math.round(startWidth)) {
            setPaneWidthOverride(resizeId, previewWidth)
          }
        } finally {
          clearPreview()
        }
      },
      onCancel: clearPreview
    })
  }

  // With a sash the detail side gets a relative wrapper so the seam handle can
  // sit on the boundary itself (junction-owned, like the shell's sashes).
  const [list, ...rest] = Children.toArray(children)

  return (
    <div className={cn('flex h-full min-h-0 flex-col', resizeId && 'stardust-master-detail-frame')}>
      <div
        className={cn(
          'grid min-h-0 min-w-0 flex-1 grid-cols-1',
          resizeId
            ? 'stardust-master-detail-grid'
            : split === 'wide'
              ? MASTER_DETAIL_WIDE_COLS
              : 'sm:grid-cols-[14rem_minmax(0,1fr)]'
        )}
        data-md-split={split}
        ref={gridRef}
        style={
          {
            '--md-min-list': `${SPLIT_MIN_LEFT_PX}px`,
            '--md-min-detail': `${SPLIT_MIN_RIGHT_PX}px`,
            '--md-split': override !== undefined ? `${override}px` : undefined
          } as CSSProperties
        }
      >
        {resizeId ? (
          <>
            {list}
            <div className="relative grid min-h-0 min-w-0">
              <div
                className="stardust-master-detail-sash group/vsash absolute inset-y-0 left-0 z-10 w-2 -translate-x-1/2 touch-none cursor-col-resize"
                onDoubleClick={() => setPaneWidthOverride(resizeId, undefined)}
                onPointerDown={startSplitDrag}
              >
                <div
                  className={cn(
                    'absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors',
                    dragging ? 'bg-(--ui-stroke-secondary)' : 'group-hover/vsash:bg-(--ui-stroke-secondary)'
                  )}
                />
              </div>
              {rest}
            </div>
          </>
        ) : (
          children
        )}
      </div>
      {pane}
    </div>
  )
}

export function ListColumn({ children, header }: { children: ReactNode; header?: ReactNode }) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  useScrollFade(viewportRef, contentRef)

  return (
    <aside className="flex min-h-0 flex-col p-2">
      {header}
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]" ref={viewportRef}>
        <div className="flow-root min-h-full" ref={contentRef}>
          {children}
        </div>
      </div>
    </aside>
  )
}

// `footer` pins one quiet caption below the scroll (e.g. "changes apply to
// new sessions") so per-item detail components never repeat it themselves.
// `actionBar` pins a real control row (save/toggle) below the scroll instead.
export function DetailColumn({
  actionBar,
  children,
  footer
}: {
  actionBar?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  useScrollFade(viewportRef, contentRef)

  return (
    <main className="flex min-h-0 flex-col overflow-hidden">
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]" ref={viewportRef}>
        <div className="mx-auto max-w-2xl space-y-5 px-5 py-4" ref={contentRef}>
          {children}
        </div>
      </div>
      {footer && (
        <div className="mx-auto w-full max-w-2xl shrink-0 px-5 pb-3 pt-1.5 text-right text-[0.65rem] text-muted-foreground/50">
          {footer}
        </div>
      )}
      {actionBar && (
        <footer className="shrink-0 bg-(--ui-chat-surface-background) px-5 py-2.5">
          <div className="mx-auto flex max-w-2xl flex-wrap items-center gap-2">{actionBar}</div>
        </footer>
      )}
    </main>
  )
}

// Full-bleed docked bottom pane: title strip + actions + close, drag-resizable
// on its top edge like every other pane (height persisted through the same
// pane-state store the terminal uses). No min height — drag (or the chevron)
// collapses it down to just the header. Content swaps freely: JSON editor
// today, stdio/log viewers tomorrow.
const DETAIL_PANE_DEFAULT_BODY_PX = 288
const DETAIL_PANE_MAX_VH = 0.7
const DETAIL_PANE_COLLAPSED_PX = 4

// Ghost icon-button on the kebab-trigger scale (pane headers, list-strip menu,
// per-server MCP actions, JSON editor format button). MUST stay a class string
// (not a CSS @utility): the leading `size-5` is what tailwind-merge uses to
// strip <Button size="icon">'s larger built-in size — a custom utility class
// isn't size-merge-aware, so Button's icon size would leak and blow it up.
// Compose extra state (data-[state=open], hover:text-destructive) with cn().
export const ICON_BUTTON =
  'size-5 cursor-pointer rounded-[4px] text-muted-foreground/70 hover:bg-(--ui-control-active-background) hover:text-foreground'

export function DetailPane({
  actions,
  children,
  defaultCollapsed = false,
  defaultHeight = DETAIL_PANE_DEFAULT_BODY_PX,
  id,
  onClose,
  title
}: {
  actions?: ReactNode
  children: ReactNode
  /** Start collapsed to the header the first time this pane is ever shown.
   *  Only seeds when the id has no saved state — a later expand/collapse
   *  persists and wins, so it's "collapsed by default", not "always collapsed". */
  defaultCollapsed?: boolean
  /** Default body height in px (before any user resize). */
  defaultHeight?: number
  /** Pane-store key — height overrides persist under it. */
  id: string
  /** Omit for permanent panes (collapsible to the header, never removed). */
  onClose?: () => void
  title: ReactNode
}) {
  const { t } = useI18n()
  const override = useStore($paneHeightOverride(id))
  const bodyRef = useRef<HTMLDivElement>(null)
  const startResize = usePointerResize(id)

  useEffect(() => {
    if (defaultCollapsed && $paneState(id).get() === undefined) {
      setPaneHeightOverride(id, 0)
    }
  }, [defaultCollapsed, id])

  const height = override ?? defaultHeight
  const collapsed = height <= DETAIL_PANE_COLLAPSED_PX
  // Sash drag mirrors the shell's y-axis pane resize: pointer capture on the
  // top edge, clamped to [0, 70vh]; double-click resets to the default.
  const [dragging, setDragging] = useState(false)

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const body = bodyRef.current

    if (!body || event.button !== 0) {
      return
    }
    const max = Math.round(body.ownerDocument.defaultView!.innerHeight * DETAIL_PANE_MAX_VH)
    let previewHeight = height
    const clearPreview = () => body.style.removeProperty('--detail-pane-preview-height')

    startResize(event, {
      axis: 'y',
      onActiveChange: setDragging,
      onPreview: delta => {
        previewHeight = Math.min(max, Math.max(0, Math.round(height - delta)))
        body.style.setProperty(
          '--detail-pane-preview-height',
          `${previewHeight <= DETAIL_PANE_COLLAPSED_PX ? 0 : previewHeight}px`
        )
      },
      onCommit: () => {
        try {
          if (previewHeight !== height) {
            setPaneHeightOverride(id, previewHeight)
          }
        } finally {
          clearPreview()
        }
      },
      onCancel: clearPreview
    })
  }

  return (
    <section className="relative flex shrink-0 flex-col border-t border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background)">
      <div
        className="group/sash absolute inset-x-0 top-0 z-10 h-2 -translate-y-1/2 touch-none cursor-row-resize"
        onDoubleClick={() => setPaneHeightOverride(id, undefined)}
        onPointerDown={startDrag}
      >
        <div
          className={cn(
            'absolute inset-x-0 top-1/2 h-px -translate-y-1/2 transition-colors',
            dragging ? 'bg-(--ui-stroke-secondary)' : 'group-hover/sash:bg-(--ui-stroke-secondary)'
          )}
        />
      </div>
      <header className="flex h-9 shrink-0 items-center gap-2 px-3">
        <span className="min-w-0 truncate text-xs font-medium text-foreground">{title}</span>
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {actions}
          <Tip label={collapsed ? t.common.expand : t.common.collapse}>
            <Button
              aria-expanded={!collapsed}
              aria-label={collapsed ? t.common.expand : t.common.collapse}
              className={ICON_BUTTON}
              onClick={() => setPaneHeightOverride(id, collapsed ? undefined : 0)}
              size="icon"
              variant="ghost"
            >
              <Codicon name={collapsed ? 'chevron-up' : 'chevron-down'} size="0.8125rem" />
            </Button>
          </Tip>
          {onClose && (
            <Button aria-label={t.common.close} className={ICON_BUTTON} onClick={onClose} size="icon" variant="ghost">
              <Codicon name="close" size="0.8125rem" />
            </Button>
          )}
        </div>
      </header>
      <div
        className="min-h-0 overflow-hidden"
        ref={bodyRef}
        style={{ height: `var(--detail-pane-preview-height, ${collapsed ? 0 : height}px)` }}
      >
        {children}
      </div>
    </section>
  )
}

// One-line control strip pinned above the list: sort/primary action on the
// left, overflow kebab on the right.
export function ListStrip({ left, right }: { left?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-1 flex h-6 shrink-0 items-center justify-between gap-2 pl-2 pr-1">
      <div className="flex min-w-0 items-center gap-1.5">{left}</div>
      <div className="flex shrink-0 items-center gap-1.5">{right}</div>
    </div>
  )
}

export interface ListStripMenuItem {
  disabled?: boolean
  label: string
  onSelect: () => void
}

export interface ListStripMenuToggle {
  checked: boolean
  disabled?: boolean
  label: string
  onToggle: (checked: boolean) => void
}

// Overflow kebab for list-wide actions. `toggle` renders as the first row —
// one label + switch line covering enable-all/disable-all (checked = every
// visible item on; mixed reads as off so one flip always means "all on").
export function ListStripMenu({
  items = [],
  label,
  toggle
}: {
  items?: ListStripMenuItem[]
  label: string
  toggle?: ListStripMenuToggle
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={label}
          className={cn(
            ICON_BUTTON,
            'data-[state=open]:bg-(--ui-control-active-background) data-[state=open]:text-foreground'
          )}
          size="icon"
          variant="ghost"
        >
          <Codicon name="kebab-vertical" size="0.8125rem" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44" sideOffset={6}>
        {toggle && (
          <DropdownMenuItem
            disabled={toggle.disabled}
            onSelect={event => {
              // Keep the menu open so the switch is seen flipping.
              event.preventDefault()
              toggle.onToggle(!toggle.checked)
            }}
          >
            <span className="min-w-0 flex-1 truncate">{toggle.label}</span>
            <Switch
              checked={toggle.checked}
              className={cn('pointer-events-none shrink-0', !toggle.checked && 'opacity-60')}
              size="xs"
              tabIndex={-1}
            />
          </DropdownMenuItem>
        )}
        {items.map(item => (
          <DropdownMenuItem disabled={item.disabled} key={item.label} onSelect={item.onSelect}>
            {item.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function ListStripButton({
  active,
  children,
  disabled,
  onClick
}: {
  active?: boolean
  children: ReactNode
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      className={cn(
        'cursor-pointer text-[0.68rem] font-medium transition-colors disabled:opacity-40',
        active ? 'text-foreground' : 'text-muted-foreground/70 hover:text-foreground'
      )}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  )
}

interface CapRowProps {
  /** Rendered in the switch slot instead of the Switch (e.g. an Install
   *  button for not-yet-installed catalog rows). */
  action?: ReactNode
  active: boolean
  busy?: boolean
  enabled: boolean
  meta?: ReactNode
  onSelect: () => void
  onToggle?: (checked: boolean) => void
  rowId?: string
  /** Second line under the name (category, description, status). Rows grow to h-11. */
  subtitle?: ReactNode
  title: string
  toggleLabel?: string
}

// The one row used by all three lists. Fixed height, always-visible switch —
// state reads from the switch + dimmed title, toggling never requires
// selecting first. Off rows dim; the switch itself dims when off.
export function CapRow({
  action,
  active,
  busy,
  enabled,
  meta,
  onSelect,
  onToggle,
  rowId,
  subtitle,
  title,
  toggleLabel
}: CapRowProps) {
  return (
    <div
      className={cn(
        // content-visibility:auto lets the browser skip layout/paint for
        // offscreen rows — the Capabilities lists routinely hold 80+ entries.
        // Row height is already fixed (h-8/h-11), so skipped rows keep their
        // exact size and scrollbar geometry never jumps.
        'group/row row-hover flex w-full shrink-0 items-center rounded-md [content-visibility:auto] hover:text-foreground',
        subtitle ? 'h-11' : 'h-8',
        active ? 'bg-(--ui-row-active-background) text-foreground' : 'text-(--ui-text-secondary)'
      )}
      id={rowId}
    >
      <RowButton
        className="flex h-full min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-md pl-2 pr-1.5 text-left"
        onClick={onSelect}
      >
        <span className="min-w-0 flex-1">
          <span
            className={cn(
              'block truncate text-[0.78rem]',
              enabled ? 'font-medium text-foreground/85' : 'font-normal text-muted-foreground/60'
            )}
          >
            {title}
          </span>
          {subtitle != null && (
            <span className="flex min-w-0 items-center gap-1 text-[0.62rem] text-muted-foreground/50">
              {typeof subtitle === 'string' ? <span className="truncate">{subtitle}</span> : subtitle}
            </span>
          )}
        </span>
        {meta != null && (
          <span className="shrink-0 rounded bg-(--ui-bg-quinary) px-1 py-px text-[0.6rem] tabular-nums leading-3.5 text-(--ui-text-tertiary)">
            {meta}
          </span>
        )}
      </RowButton>
      {action != null ? (
        <span className="mr-1.5 flex shrink-0 items-center">{action}</span>
      ) : (
        <Switch
          aria-label={toggleLabel ?? title}
          checked={enabled}
          className={cn('mr-1.5 shrink-0 cursor-pointer', !enabled && 'opacity-60')}
          disabled={busy}
          onCheckedChange={onToggle}
          size="xs"
          title={toggleLabel ?? title}
        />
      )}
    </div>
  )
}
