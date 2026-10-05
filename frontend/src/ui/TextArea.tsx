import { forwardRef, type ReactNode, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'
import { describedBy, fieldBorderClasses, FieldShell, useFieldIds, type FieldShellProps } from './Field'

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement>, FieldShellProps {
  /** field — обычное поле с рамкой; panel — крупное серое поле брифа (Figma 40:32, без рамки, радиус 16) */
  variant?: 'field' | 'panel'
  /** Нижняя строка под полем панели: счётчик знаков и пояснение (слева/справа) */
  footerLeft?: ReactNode
  footerRight?: ReactNode
  wrapperClassName?: string
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { label, hint, error, variant = 'field', footerLeft, footerRight, className, wrapperClassName, id, rows = 4, ...rest },
  ref,
) {
  const ids = useFieldIds(id)
  const area = (
    <textarea
      ref={ref}
      id={ids.id}
      rows={rows}
      aria-invalid={error ? true : undefined}
      aria-describedby={describedBy(ids, hint, error)}
      {...rest}
      className={cn(
        variant === 'field'
          ? [fieldBorderClasses(!!error), 'min-h-[96px] px-3.5 py-3 t-body']
          : [
              'block w-full resize-y bg-transparent text-ink t-lead placeholder:text-ink-3',
              'focus-visible:outline-none',
            ],
        className,
      )}
    />
  )

  if (variant === 'field') {
    return (
      <FieldShell ids={ids} label={label} hint={hint} error={error} className={wrapperClassName}>
        {area}
      </FieldShell>
    )
  }

  return (
    <FieldShell ids={ids} label={label} hint={hint} error={error} className={wrapperClassName}>
      <div
        className={cn(
          'flex flex-col gap-3 rounded-panel-lg bg-panel px-5 pb-4 pt-5 sm:px-6 sm:pt-[22px] sm:pb-[18px]',
          'focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-brand',
          error && 'outline outline-[1.5px] outline-danger',
        )}
      >
        {area}
        {(footerLeft || footerRight) && (
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 t-small text-ink-3">
            <span>{footerLeft}</span>
            <span>{footerRight}</span>
          </div>
        )}
      </div>
    </FieldShell>
  )
})
