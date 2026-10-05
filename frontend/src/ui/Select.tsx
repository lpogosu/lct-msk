import { forwardRef, type ReactNode, type SelectHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'
import { describedBy, fieldBorderClasses, FieldShell, useFieldIds, type FieldShellProps } from './Field'

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'>, FieldShellProps {
  wrapperClassName?: string
  children: ReactNode
}

/** Выпадающий список на нативном <select> (доступность и мобильные браузеры «из коробки») в стиле полей «Трассы». */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, error, className, wrapperClassName, id, children, ...rest },
  ref,
) {
  const ids = useFieldIds(id)
  return (
    <FieldShell ids={ids} label={label} hint={hint} error={error} className={wrapperClassName}>
      <div className="relative">
        <select
          ref={ref}
          id={ids.id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(ids, hint, error)}
          {...rest}
          className={cn(fieldBorderClasses(!!error), 'h-12 appearance-none pl-3.5 pr-10 t-body', className)}
        >
          {children}
        </select>
        <svg
          width="14"
          height="14"
          viewBox="0 0 14 14"
          fill="none"
          aria-hidden="true"
          className="pointer-events-none absolute right-3.5 top-1/2 -translate-y-1/2 text-ink-3"
        >
          <path d="M3 5.5l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    </FieldShell>
  )
})
