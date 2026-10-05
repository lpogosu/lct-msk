import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface AlertProps {
  tone?: 'info' | 'danger' | 'success'
  title?: string
  children?: ReactNode
  className?: string
}

/** Встроенное сообщение в форме или на экране. Красный — только для ошибок («против», срок). */
export function Alert({ tone = 'info', title, children, className }: AlertProps) {
  const danger = tone === 'danger'
  return (
    <div
      role={danger ? 'alert' : 'status'}
      className={cn(
        'flex gap-3 rounded-panel px-4 py-3.5',
        danger ? 'bg-[#FFF1F3]' : 'bg-tint',
        className,
      )}
    >
      <span aria-hidden="true" className="mt-[7px] flex shrink-0 items-center">
        <span className={cn('h-[1.5px] w-3', danger ? 'bg-danger' : 'bg-brand')} />
        <span className={cn('h-2 w-2 rounded-full', danger ? 'bg-danger' : 'bg-brand')} />
      </span>
      <div className="min-w-0">
        {title && <p className={cn('t-body-strong', danger ? 'text-danger-text' : 'text-ink')}>{title}</p>}
        {children && <div className={cn('t-body', danger ? 'text-danger-text' : 'text-ink-2')}>{children}</div>}
      </div>
    </div>
  )
}
