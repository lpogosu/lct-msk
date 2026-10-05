import type { ReactNode } from 'react'
import { Button } from './Button'
import { EmptyState } from './EmptyState'

export interface WorkInProgressProps {
  /** Название раздела: «Подборка», «Банк кандидатов» */
  section?: string
  description?: ReactNode
  /** Куда вернуть пользователя; без этого кнопки нет */
  backTo?: string
  backLabel?: string
  /** Номер узла Figma экрана (страница «v2 · Трасса») — подсказка разработчику, показывается только в dev */
  figma?: string
  className?: string
}

/**
 * Спокойное состояние для ещё не собранных экранов (вместо битых кнопок и 404).
 * Экран-агент заменяет этот компонент в `src/app/routes.tsx` на настоящую страницу.
 */
export function WorkInProgress({
  section,
  description,
  backTo,
  backLabel = 'Вернуться',
  figma,
  className,
}: WorkInProgressProps) {
  return (
    <div className={className}>
      <EmptyState
        image="detector"
        headingLevel={1}
        title={section ? `${section}: раздел в работе` : 'Раздел в работе'}
        description={description ?? 'Мы доделываем этот экран. Остальные разделы уже работают.'}
        action={
          backTo ? (
            <Button variant="outline" to={backTo}>
              {backLabel}
            </Button>
          ) : undefined
        }
      />
      {import.meta.env.DEV && figma && (
        <p className="mt-2 text-center t-small text-ink-3">
          Figma: <span className="font-mono">{figma}</span> (страница «v2 · Трасса»)
        </p>
      )}
    </div>
  )
}
