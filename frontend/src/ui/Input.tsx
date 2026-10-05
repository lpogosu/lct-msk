import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { describedBy, fieldBorderClasses, FieldShell, useFieldIds, type FieldShellProps } from './Field'

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'>, FieldShellProps {
  /** Моноширинный шрифт — для ответов-кода и 6-значного кода из письма */
  mono?: boolean
  /** Элемент справа внутри поля (например, «Показать») */
  adornment?: ReactNode
  wrapperClassName?: string
}

/** Поле ввода. Рамка 1 px steel → 1,5 px brand в фокусе; ошибка — красная рамка и текст под полем. */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, mono, adornment, className, wrapperClassName, id, ...rest },
  ref,
) {
  const ids = useFieldIds(id)
  return (
    <FieldShell ids={ids} label={label} hint={hint} error={error} className={wrapperClassName}>
      <div className="relative">
        <input
          ref={ref}
          id={ids.id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(ids, hint, error)}
          {...rest}
          className={cn(
            fieldBorderClasses(!!error),
            'h-12 px-3.5',
            mono ? 't-code' : 't-body',
            adornment && 'pr-20',
            className,
          )}
        />
        {adornment && <div className="absolute inset-y-0 right-2 flex items-center">{adornment}</div>}
      </div>
    </FieldShell>
  )
})
