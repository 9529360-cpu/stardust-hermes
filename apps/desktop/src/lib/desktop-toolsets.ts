import type { ToolsetInfo } from '@/types/hermes'

// Curation for the desktop "Skills & Tools → Toolsets" list.
//
// `GET /api/tools/toolsets` returns the full CONFIGURABLE_TOOLSETS set with no
// desktop-specific filter — so it surfaces entries that don't belong in a flat
// per-user toggle list on the desktop: platform-coupled toolsets (which
// `hermes tools` already platform-restricts on the CLI) and internal plumbing
// that isn't a user-facing capability. Mirror the curation approach used for
// slash commands (`desktop-slash-commands.ts`): one documented block-list, one
// predicate. Hiding a toolset only removes its row — its enabled state and
// runtime gating are untouched.
const DESKTOP_HIDDEN_TOOLSETS = new Set([
  // Platform-coupled — only meaningful when that platform is the active
  // adapter; `hermes tools` restricts these off the CLI too.
  'discord',
  'discord_admin',
  'yuanbao',
  // Internal plumbing, not a user capability toggle.
  'context_engine',
  'moa'
])

export function isDesktopToolsetVisible(name: string): boolean {
  return !DESKTOP_HIDDEN_TOOLSETS.has(name)
}

/** A toolset row is a tool toggle, so it needs tools behind it. One with none is
 *  a config-only capability (speech-to-text today): its on/off switch and
 *  provider live in Settings → Voice, and a row here would show a live switch
 *  next to "0 tools" for something that is not a tool. */
export function isDesktopToolsetRow(toolset: Pick<ToolsetInfo, 'name' | 'tools'>): boolean {
  return isDesktopToolsetVisible(toolset.name) && Array.isArray(toolset.tools) && toolset.tools.length > 0
}
