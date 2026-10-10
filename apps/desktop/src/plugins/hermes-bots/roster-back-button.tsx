import { Button, Codicon, host, Tip } from '@hermes/plugin-sdk'

/**
 * The way out of the roster. The roster is a tab in the sessions zone, and the
 * product navigation hides that zone's tab strip (personal-product-nav.css), so
 * while the roster is showing nothing else on screen reaches the nav. Revealing
 * the sessions pane makes it the zone's active tab again; the plugin's own
 * visibility listener then releases the bot workspace and Cronjobs tile.
 */
export function RosterBackButton({ label }: { label: string }) {
  return (
    <Tip label={label}>
      <Button
        aria-label={label}
        className="rounded-md text-(--ui-text-tertiary) hover:text-foreground"
        onClick={() => host.revealPane('sessions')}
        size="icon-xs"
        variant="ghost"
      >
        <Codicon name="chevron-left" />
      </Button>
    </Tip>
  )
}
