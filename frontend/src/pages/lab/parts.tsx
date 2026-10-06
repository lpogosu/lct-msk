import type { ReactNode } from 'react'
import { ErrorBlock } from '@/features/candidate/components/QueryBoundary'
import { cn } from '@/lib/cn'
import { Skeleton } from '@/ui'

/** Шапка экрана лаборатории: подпись, заголовок «Трассы» (JetBrains Mono, фиолетовый), пояснение, справа — действия. */
export function LabHead({
  caption,
  title,
  lead,
  actions,
  aside,
}: {
  caption?: ReactNode
  title: string
  lead?: ReactNode
  actions?: ReactNode
  aside?: ReactNode
}) {
  return (
    <header className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 flex-col gap-3">
        {caption && <p className="t-small text-ink-3">{caption}</p>}
        <h1 className="t-display text-brand">{title}</h1>
        {lead && <p className="t-lead max-w-[820px] text-ink-2">{lead}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-3">{actions}</div>}
      {aside}
    </header>
  )
}

export function LabSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-4" role="status" aria-label="Загрузка">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-14 w-full rounded-panel" />
      ))}
    </div>
  )
}

export { ErrorBlock as LabError }

/** Тонкая таблица без рамок: заголовок hairline-ом, строки разделены hairline (стиль «Трассы»). */
export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full border-collapse text-left t-body">
        <thead>
          <tr className="border-b border-hairline">
            {head.map((h, i) => (
              <th key={i} scope="col" className="whitespace-nowrap px-3 py-2.5 first:pl-0 last:pr-0 t-small font-medium text-ink-3">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Td({ children, className, mono }: { children?: ReactNode; className?: string; mono?: boolean }) {
  return (
    <td className={cn('px-3 py-3 align-top first:pl-0 last:pr-0', mono && 'font-mono text-[13px] tabular-nums', className)}>
      {children}
    </td>
  )
}

/** Плашка-метка статуса: нейтральная, фиолетовая или (только для «против»/срока) красная. */
export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'brand' | 'danger' }) {
  return (
    <span
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-pill px-2.5 py-0.5 t-small',
        tone === 'brand' && 'bg-tint text-brand',
        tone === 'danger' && 'bg-[#FFF1F3] text-danger-text',
        tone === 'neutral' && 'bg-panel text-ink-2',
      )}
    >
      {children}
    </span>
  )
}
