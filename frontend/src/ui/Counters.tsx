import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface CounterItem {
  /** Число (JetBrains Mono, 32 px) */
  value: ReactNode
  label: string
  /** brand — выделить число (в Figma: «2 новых с прошлой версии») */
  tone?: 'default' | 'brand'
}

/**
 * Ряд чисел с подписями, разделённых вертикальными hairline (Figma 28:238 «Счётчики»).
 * На узком экране — сетка из двух колонок, разделитель остаётся между колонками.
 */
export function Counters({ items, className }: { items: CounterItem[]; className?: string }) {
  return (
    <dl className={cn('grid grid-cols-2 gap-y-6 sm:flex sm:flex-wrap sm:gap-y-4', className)}>
      {items.map((it, i) => (
        <div
          key={i}
          className={cn(
            'flex min-w-0 flex-col-reverse justify-end gap-1 pr-6',
            'max-sm:even:border-l max-sm:even:border-hairline max-sm:even:pl-6',
            'sm:border-l sm:border-hairline sm:pl-6 sm:first:border-l-0 sm:first:pl-0',
          )}
        >
          {/* в разметке подпись (dt) идёт первой, на экране число стоит над ней: flex-col-reverse */}
          <dt className="t-small text-ink-2">{it.label}</dt>
          <dd className={cn('t-num-l m-0', it.tone === 'brand' ? 'text-brand' : 'text-ink')}>{it.value}</dd>
        </div>
      ))}
    </dl>
  )
}
