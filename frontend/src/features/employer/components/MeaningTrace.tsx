import { cn } from '@/lib/cn'
import { Skeleton, TraceDiagram, type TraceDiagramRow } from '@/ui'
import type { MeaningRow } from '../brief'
import { BlockTitle } from './common'

/**
 * «Как мы поняли» (Figma 40:81, 40:83): слева цитата из брифа, справа — что из неё получилось.
 * Сплошная трасса — уверенно, пунктир — неуверенное место (его мы спрашиваем, а не угадываем).
 * Наведение или фокус на строку подсвечивает фразу в тексте.
 */
export function MeaningTrace({
  rows,
  loading,
  activeKey,
  onActive,
}: {
  rows: MeaningRow[]
  loading?: boolean
  activeKey: string | null
  onActive: (key: string | null) => void
}) {
  const traceRows: TraceDiagramRow[] = rows.map((r) => {
    const handlers = {
      onMouseEnter: () => onActive(r.key),
      onMouseLeave: () => onActive(null),
      onFocus: () => onActive(r.key),
      onBlur: () => onActive(null),
    }
    return {
      key: r.key,
      end: r.uncertain ? 'none' : 'open',
      label: (
        <span tabIndex={0} className={cn('rounded-sm', activeKey === r.key && 'text-brand')} {...handlers}>
          «{r.label}»
        </span>
      ),
      evidence: (
        <span tabIndex={0} className="rounded-sm" {...handlers}>
          {r.meaning}
          {r.uncertain && <span className="sr-only"> (неуверенно)</span>}
        </span>
      ),
    }
  })

  return (
    <section aria-labelledby="meaning-title" className="flex flex-col gap-3">
      <BlockTitle>
        <span id="meaning-title">Как мы поняли</span>
      </BlockTitle>
      {rows.length === 0 ? (
        loading ? (
          <div role="status" aria-label="Разбираем текст" className="flex flex-col gap-3 py-1">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-5/6" />
            <Skeleton className="h-8 w-2/3" />
          </div>
        ) : (
          <p className="t-body text-ink-3">
            Напишите несколько предложений: чем занимается команда, какие навыки нужны, формат и вилка. Здесь появится
            разбор по каждой фразе.
          </p>
        )
      ) : (
        <div className={cn('transition-opacity', loading && 'opacity-60')}>
          <TraceDiagram
            rows={traceRows}
            monoLabels
            labelWidth={168}
            rowHeight={44}
            aria-label="Как мы поняли бриф: фраза и её смысл"
          />
        </div>
      )}
      <p className="t-small text-ink-3">
        Наведите на строку — подсветим фразу в тексте. Неуверенные места (пунктир) мы спрашиваем, а не угадываем.
      </p>
    </section>
  )
}
