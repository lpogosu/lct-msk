import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, unwrap } from '@/api/client'
import { Alert } from '@/ui'

/**
 * Прогрев пула одноразовых заданий после `docker compose up` (#14): вместо ошибки — «Готовим одноразовые задания…»
 * с прогрессом. Опрашивает GET /api/health/pool, пока пул не готов; когда готов — вызывает onReady и скрывается.
 */
export function PoolWarmup({ spec, onReady }: { spec?: string; onReady?: () => void }) {
  const q = useQuery({
    queryKey: ['health', 'pool', spec ?? 'all'],
    queryFn: () => unwrap(api.GET('/api/health/pool', { params: { query: spec ? { specialization: spec } : {} } })),
    refetchInterval: (query) => (query.state.data?.ready ? false : 3000),
    retry: true,
  })
  const ready = q.data?.ready === true
  useEffect(() => {
    if (ready) onReady?.()
  }, [ready, onReady])
  if (ready) return null
  const pct = Math.round((q.data?.progress ?? 0) * 100)
  return (
    <Alert title="Готовим одноразовые задания… около 2 минут">
      <p>Банк заданий разогревается после запуска: у каждого кандидата свои клоны, поэтому их создаётся запас. Страница обновится сама.</p>
      <div
        role="progressbar"
        aria-label="Готовность пула заданий"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="mt-3 h-2 w-full overflow-hidden rounded-pill bg-hairline"
      >
        <div className="h-full bg-brand transition-[width] duration-500" style={{ width: `${Math.max(pct, 4)}%` }} />
      </div>
      <p className="mt-1 t-small text-ink-3">
        {q.data ? `Готово ${q.data.families_ready} из ${q.data.families_total} семейств заданий` : 'Проверяем готовность…'}
      </p>
    </Alert>
  )
}
