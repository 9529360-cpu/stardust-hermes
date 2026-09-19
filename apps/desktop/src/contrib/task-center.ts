/**
 * Renderer-only extension point for assistant Task Center sections.
 *
 * Contributions render their own read-only projection from the source that
 * already owns the data. The area deliberately carries no shared task DTO:
 * plugins keep lifecycle truth in their existing backend/query owner and
 * disappear cleanly when disabled.
 */
export const TASK_CENTER_AREAS = {
  sections: 'taskCenter.sections'
} as const
