import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { errorMessage, isApiException } from '@/api/errors'
import { Alert, Button, EmptyState, Skeleton } from '@/ui'

/** Скелетон по умолчанию: заголовок + несколько плашек. */
export function DefaultSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-4" role="status" aria-label="Загрузка">
      <Skeleton className="h-8 w-2/3" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-20 w-full rounded-panel" />
      ))}
    </div>
  )
}

export function ErrorBlock({
  error,
  onRetry,
  compact,
  title = 'Не удалось загрузить',
}: {
  error: unknown
  onRetry?: () => void
  compact?: boolean
  title?: string
}) {
  if (isApiException(error) && error.isNotImplemented) {
    return (
      <EmptyState
        size="compact"
        image="detector"
        title="Раздел в работе"
        description="Мы доделываем эту часть. Остальные разделы уже работают."
      />
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <Alert tone="danger" title={title}>
        {errorMessage(error)}
      </Alert>
      {onRetry && (
        <div className={compact ? '' : 'flex justify-center'}>
          <Button variant="outline" size="sm" onClick={onRetry}>
            Повторить
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * Единое поведение трёх состояний запроса: загрузка (скелетон), ошибка («Повторить», 501 — «раздел в работе»),
 * данные. Пустоту рисует вызывающий — внутри `children`.
 */
export function QueryBoundary<T>({
  query,
  children,
  skeleton,
  errorTitle,
  compact,
}: {
  query: UseQueryResult<T, Error>
  children: (data: T) => ReactNode
  skeleton?: ReactNode
  errorTitle?: string
  compact?: boolean
}) {
  if (query.isPending) return <>{skeleton ?? <DefaultSkeleton />}</>
  if (query.isError) {
    return <ErrorBlock error={query.error} onRetry={() => void query.refetch()} title={errorTitle} compact={compact} />
  }
  return <>{children(query.data)}</>
}
