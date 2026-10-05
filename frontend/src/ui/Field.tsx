import { useId, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface FieldShellProps {
  label?: ReactNode
  hint?: ReactNode
  error?: ReactNode
  className?: string
}

export interface FieldIds {
  id: string
  hintId: string
  errorId: string
}

/** Идентификаторы для связи подписи, подсказки и ошибки с полем (aria-describedby). */
export function useFieldIds(explicitId?: string): FieldIds {
  const auto = useId()
  const id = explicitId ?? auto
  return { id, hintId: `${id}-hint`, errorId: `${id}-error` }
}

export function describedBy(ids: FieldIds, hint: ReactNode, error: ReactNode): string | undefined {
  const list = [error ? ids.errorId : null, hint ? ids.hintId : null].filter(Boolean)
  return list.length ? (list.join(' ') as string) : undefined
}

/** Общая обёртка поля: подпись сверху, подсказка и ошибка снизу (Figma 34:35 «Ваш ответ»). */
export function FieldShell({
  ids,
  label,
  hint,
  error,
  className,
  children,
}: FieldShellProps & { ids: FieldIds; children: ReactNode }) {
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {label && (
        <label htmlFor={ids.id} className="t-label text-ink-2">
          {label}
        </label>
      )}
      {children}
      {error ? (
        <p id={ids.errorId} role="alert" className="t-small text-danger-text">
          {error}
        </p>
      ) : hint ? (
        <p id={ids.hintId} className="t-small text-ink-3">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

/** Единый вид границы поля: steel → фиолетовая 1,5 px при фокусе, красная при ошибке. */
export function fieldBorderClasses(invalid?: boolean): string {
  return cn(
    'w-full rounded-cell border bg-white text-ink placeholder:text-ink-3 transition-colors',
    'disabled:cursor-not-allowed disabled:bg-panel disabled:text-ink-3',
    invalid
      ? 'border-[1.5px] border-danger'
      : 'border-steel hover:border-ink-3 focus:border-[1.5px] focus:border-brand',
    // при фокусе рамка уже фиолетовая и толще — внешний контур не нужен
    'focus-visible:outline-none',
  )
}
