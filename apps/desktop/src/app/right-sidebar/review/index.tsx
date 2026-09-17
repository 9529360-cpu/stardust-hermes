import { useStore } from '@nanostores/react'

import { FileDiffPanel } from '@/components/chat/diff-lines'
import { DiffSkeleton, TreeSkeleton } from '@/components/chat/skeletons'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { DiffCount } from '@/components/ui/diff-count'
import { Tip } from '@/components/ui/tooltip'
import { useDelayedTrue } from '@/hooks/use-delayed-true'
import { useI18n } from '@/i18n'
import { displayPath } from '@/lib/display-path'
import { cn } from '@/lib/utils'
import { $panesFlipped } from '@/store/layout'
import { notifyError } from '@/store/notifications'
import {
  $reviewDiff,
  $reviewDiffLoading,
  $reviewFiles,
  $reviewIsRepo,
  $reviewLoading,
  $reviewRevertTarget,
  $reviewSelectedPath,
  $reviewTreeMode,
  cancelRevert,
  clearReviewSelection,
  closeReview,
  confirmRevert,
  refreshReview,
  requestRevert,
  stageReviewFile,
  toggleReviewTreeMode,
  unstageReviewFile
} from '@/store/review'

import { PaneEmptyState } from '../index'

import { ReviewFileTree } from './file-tree'
import { ReviewShipBar } from './ship-bar'

const ACTION_BTN = 'size-6'

export function ReviewPane() {
  const { t } = useI18n()
  const c = t.statusStack.coding
  const panesFlipped = useStore($panesFlipped)
  const files = useStore($reviewFiles)
  const loading = useStore($reviewLoading)
  const isRepo = useStore($reviewIsRepo)
  const selectedPath = useStore($reviewSelectedPath)
  const diff = useStore($reviewDiff)
  const diffLoading = useStore($reviewDiffLoading)
  const revertTarget = useStore($reviewRevertTarget)
  const treeMode = useStore($reviewTreeMode)

  const selectedFile = files.find(file => file.path === selectedPath)
  const hasFiles = files.length > 0
  const added = files.reduce((sum, file) => sum + file.added, 0)
  const removed = files.reduce((sum, file) => sum + file.removed, 0)
  const revertingAll = revertTarget?.path == null
  const showTreeSkeleton = useDelayedTrue(loading && !hasFiles)
  const showDiffSkeleton = useDelayedTrue(diffLoading)

  return (
    <aside
      aria-label={c.review}
      className={cn(
        'before:pointer-events-none relative flex h-full w-full min-w-0 flex-col overflow-hidden border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) text-(--ui-text-tertiary)',
        panesFlipped
          ? 'border-r shadow-[inset_-0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
          : 'border-l shadow-[inset_0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
      )}
      data-review-workspace=""
    >
      <div
        className="flex h-11 shrink-0 items-center gap-1 border-b border-(--ui-stroke-quaternary) px-2.5"
        data-review-workspace-header=""
        data-suppress-pane-reveal-side=""
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Codicon className="shrink-0 text-(--ui-text-tertiary)" name="diff" size="0.8rem" />
          <span className="truncate text-[0.72rem] font-semibold text-(--ui-text-primary)">{c.review}</span>
          {hasFiles && (
            <>
              <span className="rounded-full bg-(--ui-fill-quaternary) px-1.5 py-0.5 text-[0.58rem] font-medium text-(--ui-text-tertiary)">
                {files.length}
              </span>
              <DiffCount added={added} className="text-[0.58rem]" removed={removed} />
            </>
          )}
        </div>

        <Tip label={treeMode === 'tree' ? c.viewAsList : c.viewAsTree}>
          <Button
            aria-label={treeMode === 'tree' ? c.viewAsList : c.viewAsTree}
            className={ACTION_BTN}
            disabled={!hasFiles}
            onClick={toggleReviewTreeMode}
            size="icon-xs"
            variant="ghost"
          >
            <Codicon name={treeMode === 'tree' ? 'list-flat' : 'list-tree'} size="0.78rem" />
          </Button>
        </Tip>
        <Tip label={c.stageAll}>
          <Button
            aria-label={c.stageAll}
            className={ACTION_BTN}
            disabled={!hasFiles}
            onClick={() => void stageReviewFile(null).catch(err => notifyError(err, c.stageAll))}
            size="icon-xs"
            variant="ghost"
          >
            <Codicon name="add" size="0.78rem" />
          </Button>
        </Tip>
        <Tip label={c.revertAll}>
          <Button
            aria-label={c.revertAll}
            className={ACTION_BTN}
            disabled={!hasFiles}
            onClick={() => requestRevert(null)}
            size="icon-xs"
            variant="ghost"
          >
            <Codicon name="discard" size="0.78rem" />
          </Button>
        </Tip>
        <Tip label={t.rightSidebar.refreshTree}>
          <Button
            aria-label={t.rightSidebar.refreshTree}
            className={ACTION_BTN}
            onClick={() => void refreshReview()}
            size="icon-xs"
            variant="ghost"
          >
            <Codicon name="refresh" size="0.78rem" spinning={loading} />
          </Button>
        </Tip>
        <Tip label={c.close}>
          <Button aria-label={c.close} className={ACTION_BTN} onClick={closeReview} size="icon-xs" variant="ghost">
            <Codicon name="close" size="0.78rem" />
          </Button>
        </Tip>
      </div>

      {loading || isRepo ? (
        hasFiles ? (
          <ReviewFileTree />
        ) : showTreeSkeleton ? (
          <TreeSkeleton />
        ) : loading ? (
          <div className="min-h-0 flex-1" />
        ) : (
          <PaneEmptyState label={t.rightSidebar.noDiffs} />
        )
      ) : (
        <PaneEmptyState label={t.rightSidebar.noDiffs} />
      )}

      {selectedFile && (
        <div
          className="flex min-h-0 flex-1 flex-col border-t border-(--ui-stroke-secondary)"
          data-review-diff-panel=""
        >
          <div className="flex items-center gap-1 px-2.5 py-1.5" data-suppress-pane-reveal-side="">
            <span
              className="min-w-0 flex-1 truncate font-mono text-[0.66rem] text-(--ui-text-secondary)"
              title={displayPath(selectedFile.path)}
            >
              {displayPath(selectedFile.path)}
            </span>
            <DiffCount added={selectedFile.added} className="text-[0.64rem] leading-4" removed={selectedFile.removed} />
            <Tip label={selectedFile.staged ? c.unstage : c.stage}>
              <Button
                aria-label={selectedFile.staged ? c.unstage : c.stage}
                className={ACTION_BTN}
                onClick={() =>
                  void (
                    selectedFile.staged ? unstageReviewFile(selectedFile.path) : stageReviewFile(selectedFile.path)
                  ).catch(err => notifyError(err, c.stage))
                }
                size="icon-xs"
                variant="ghost"
              >
                <Codicon name={selectedFile.staged ? 'remove' : 'add'} size="0.8rem" />
              </Button>
            </Tip>
            <Button
              aria-label={c.close}
              className={ACTION_BTN}
              onClick={clearReviewSelection}
              size="icon-xs"
              variant="ghost"
            >
              <Codicon name="close" size="0.8rem" />
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-1 pb-1">
            {diffLoading ? (
              showDiffSkeleton ? (
                <DiffSkeleton />
              ) : null
            ) : diff ? (
              <FileDiffPanel className="mx-0 mb-0 h-full max-h-none" diff={diff} path={selectedFile.path} virtualized />
            ) : (
              <div className="py-6 text-center text-[0.66rem] text-muted-foreground/60">{c.noDiff}</div>
            )}
          </div>
        </div>
      )}

      <ReviewShipBar />

      <ConfirmDialog
        confirmLabel={revertingAll ? c.revertAll : c.revert}
        description={
          <>
            {revertingAll ? c.revertAllConfirm : c.revertConfirm}
            {!revertingAll && revertTarget?.path && (
              <span
                className="mt-2 block truncate font-mono text-[0.7rem] text-(--ui-text-secondary)"
                title={revertTarget.path}
              >
                {displayPath(revertTarget.path)}
              </span>
            )}
          </>
        }
        destructive
        dismissOnConfirm
        onClose={cancelRevert}
        onConfirm={() => confirmRevert().catch(err => void notifyError(err, c.revert))}
        open={revertTarget !== undefined}
        title={revertingAll ? c.revertAll : c.revert}
      />
    </aside>
  )
}
