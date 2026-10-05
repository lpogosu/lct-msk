import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Illustration, type IllustrationName } from './Illustration'

export interface EmptyStateProps {
  /** 3D-иллюстрация; по умолчанию empty_state */
  image?: IllustrationName | null
  title: string
  description?: ReactNode
  /** Кнопка или ссылка действия */
  action?: ReactNode
  /** compact — для вставки в карточки и списки */
  size?: 'md' | 'compact'
  /** Уровень заголовка в документе: 1 — если это весь экран, 2 — внутри раздела */
  headingLevel?: 1 | 2 | 3
  className?: string
}

/** Пустое состояние: 3D-объект, заголовок, пояснение и одно действие. Всегда говорит, что делать дальше. */
export function EmptyState({
  image = 'empty_state',
  title,
  description,
  action,
  size = 'md',
  headingLevel = 2,
  className,
}: EmptyStateProps) {
  const compact = size === 'compact'
  const Heading = `h${headingLevel}` as const
  return (
    <div className={cn('flex flex-col items-center text-center', compact ? 'gap-3 py-6' : 'gap-4 py-10', className)}>
      {image && <Illustration name={image} className={compact ? 'w-32' : 'w-48 sm:w-60'} />}
      <div className="flex max-w-[420px] flex-col gap-1.5">
        <Heading className={cn(compact ? 't-body-strong' : 't-name', 'text-ink')}>{title}</Heading>
        {description && <p className="t-body text-ink-2">{description}</p>}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}
