import { cn } from '@/lib/cn'

/** Индикатор загрузки: «узел», бегущий по кольцу. При prefers-reduced-motion вращение отключается глобально. */
export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span role={label ? 'status' : undefined} aria-label={label} className={cn('inline-flex', className)}>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="animate-spin">
        <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.5" />
        <circle cx="8" cy="2" r="2" fill="currentColor" />
      </svg>
    </span>
  )
}
