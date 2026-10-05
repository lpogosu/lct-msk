import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { useFieldIds } from './Field'

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  label: ReactNode
  /** Пояснение под подписью */
  description?: ReactNode
  error?: ReactNode
  /** Приглушённая подпись (фильтр с нулём результатов — «Junior 41» серым) */
  muted?: boolean
  wrapperClassName?: string
}

/**
 * Флажок: нативный input (клавиатура, формы, скринридер) + нарисованный квадрат 14 px как в Figma.
 * Область нажатия — вся строка с подписью.
 */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, description, error, muted, className, wrapperClassName, id, ...rest },
  ref,
) {
  const ids = useFieldIds(id)
  return (
    <div className={cn('flex flex-col gap-1', wrapperClassName)}>
      <label
        htmlFor={ids.id}
        className={cn(
          'flex cursor-pointer items-start gap-2.5 py-1 t-body',
          rest.disabled && 'cursor-not-allowed',
          muted ? 'text-ink-3' : 'text-ink',
          className,
        )}
      >
        <input
          ref={ref}
          id={ids.id}
          type="checkbox"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? ids.errorId : description ? ids.hintId : undefined}
          className="peer sr-only"
          {...rest}
        />
        <span
          aria-hidden="true"
          className={cn(
            'mt-[4px] flex h-[14px] w-[14px] shrink-0 items-center justify-center rounded-[4px] border border-steel bg-white transition-colors',
            'peer-checked:border-brand peer-checked:bg-brand',
            'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand',
            error && 'border-danger',
            'peer-disabled:opacity-50',
            '[&>svg]:opacity-0 peer-checked:[&>svg]:opacity-100',
          )}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3.5 7.2L6 9.5L10.5 4.5" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="min-w-0">{label}</span>
      </label>
      {description && !error && (
        <p id={ids.hintId} className="ml-6 t-small text-ink-3">
          {description}
        </p>
      )}
      {error && (
        <p id={ids.errorId} role="alert" className="ml-6 t-small text-danger-text">
          {error}
        </p>
      )}
    </div>
  )
})
