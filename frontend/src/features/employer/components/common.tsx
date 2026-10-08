import type { ReactNode } from 'react'
import { ApiException, errorMessage } from '@/api/errors'
import { cn } from '@/lib/cn'
import { Alert, Button, EmptyState, Skeleton, SkeletonText } from '@/ui'

/** Заголовок экрана: небольшая строка сверху, фиолетовый display, поясняющий абзац и действия справа (Figma 27:2, 42:2). */
export function PageHead({
  eyebrow,
  title,
  lead,
  actions,
  className,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  lead?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <header className={cn('flex flex-wrap items-end justify-between gap-x-8 gap-y-5', className)}>
      <div className="min-w-0 max-w-[820px]">
        {eyebrow && <p className="mb-3 t-small text-ink-3">{eyebrow}</p>}
        <h1 className="t-display text-brand [overflow-wrap:anywhere]">{title}</h1>
        {lead && <p className="mt-4 t-lead text-ink">{lead}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-x-6 gap-y-3">{actions}</div>}
    </header>
  )
}

/** Ошибка загрузки: что случилось словами и «Повторить». 501 — спокойное «раздел в работе». */
export function LoadError({
  error,
  onRetry,
  title = 'Не удалось загрузить',
  className,
}: {
  error: unknown
  onRetry?: () => void
  title?: string
  className?: string
}) {
  if (error instanceof ApiException && error.isNotImplemented) {
    return (
      <EmptyState
        image="detector"
        title="Раздел в работе"
        description="Мы доделываем этот экран. Остальные разделы уже работают."
        className={className}
      />
    )
  }
  return (
    <div className={cn('flex flex-col items-start gap-3', className)}>
      <Alert tone="danger" title={title}>
        {errorMessage(error)}
      </Alert>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  )
}

export function PageSkeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('flex flex-col gap-6', className)} role="status" aria-label="Загрузка">
      <Skeleton className="h-12 w-2/3" />
      <SkeletonText lines={2} />
      <div className="flex flex-col gap-3">
        {Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    </div>
  )
}

/** Заголовок блока внутри панелей: «Как мы поняли», «Как мы проверим». */
export function BlockTitle({ children, className }: { children: ReactNode; className?: string }) {
  return <h2 className={cn('t-body-strong text-ink', className)}>{children}</h2>
}

/** Мелкая подпись-ключ: серый label над значением. */
export function Label({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn('t-small text-ink-3', className)}>{children}</p>
}

/** Чип-метка статуса: нейтральный, фиолетовый или красный (только для сроков и «против»). */
export function Tag({
  children,
  tone = 'default',
  className,
  title,
}: {
  children: ReactNode
  tone?: 'default' | 'brand' | 'danger' | 'warn'
  className?: string
  title?: string
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-pill px-2.5 py-0.5 t-small',
        tone === 'default' && 'bg-panel text-ink-2',
        tone === 'brand' && 'bg-tint text-brand',
        tone === 'danger' && 'bg-[#FFF1F3] text-danger-text',
        tone === 'warn' && 'bg-[#FFF7DB] text-[#7A5B00]',
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Сигнал интереса кандидата (DECISIONS 24): отдельный бейдж, на Score не влияет; приходит только по открытым карточкам. */
export function InterestTag({ interest }: { interest?: { label: string; text: string; hint: string } | null }) {
  if (!interest) return null
  return (
    <Tag tone="brand" title={`${interest.text}. ${interest.hint}`}>
      {interest.label}
    </Tag>
  )
}
