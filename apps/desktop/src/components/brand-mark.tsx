import { cn } from '@/lib/utils'

// Product badge for the private Stardust desktop. Keep it asset-free so the
// mark stays sharp at every scale and no upstream artwork leaks into the UI.
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-flex size-14 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-white/12 bg-[radial-gradient(circle_at_30%_20%,rgba(116,211,255,0.28),transparent_34%),linear-gradient(145deg,#6f8cff_0%,#7d62ef_54%,#263f8f_100%)] text-white shadow-[0_10px_28px_rgba(53,71,160,0.3)]',
        className
      )}
      {...props}
    >
      <svg className="size-[68%] drop-shadow-[0_0_10px_rgba(214,229,255,0.45)]" viewBox="0 0 64 64">
        <path d="M32 5 38 26 59 32 38 38 32 59 26 38 5 32 26 26Z" fill="currentColor" />
        <path d="m49 8 2.2 6.8L58 17l-6.8 2.2L49 26l-2.2-6.8L40 17l6.8-2.2Z" fill="currentColor" opacity="0.72" />
      </svg>
    </span>
  )
}
