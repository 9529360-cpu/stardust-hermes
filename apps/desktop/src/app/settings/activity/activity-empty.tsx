/** One line of muted copy for an empty group inside a settings section. A page-level EmptyState floats too large there. */
export function ActivityEmpty({ children }: { children: string }) {
  return <p className="px-1 py-2 text-sm text-muted-foreground">{children}</p>
}
