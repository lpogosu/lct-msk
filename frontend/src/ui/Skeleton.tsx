import { cn } from '@/lib/cn'

/** Заглушка на время загрузки: серый блок с мягким бликом (при prefers-reduced-motion блик не движется). */
export function Skeleton({ className, circle }: { className?: string; circle?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'relative block overflow-hidden bg-hairline',
        circle ? 'rounded-full' : 'rounded-md',
        // блик — плоская светлая полоса, без градиента (градиент в системе только у hero-панели)
        "before:absolute before:inset-y-0 before:left-0 before:w-1/3 before:-translate-x-full before:animate-shimmer before:bg-white/60 before:content-['']",
        className,
      )}
    />
  )
}

/** Несколько строк текста; последняя короче. */
export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <span className={cn('flex flex-col gap-2', className)} role="status" aria-label="Загрузка">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('h-4', i === lines - 1 && lines > 1 ? 'w-2/3' : 'w-full')} />
      ))}
    </span>
  )
}
