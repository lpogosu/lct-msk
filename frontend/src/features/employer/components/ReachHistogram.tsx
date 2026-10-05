import { cn } from '@/lib/cn'
import { countWord, formatDecimal } from '@/lib/format'

export interface HistogramData {
  step: number
  /** [верх ступени, сколько кандидатов]; ступени склеены бэкендом так, чтобы в каждой было не меньше 3 человек */
  bins: number[][]
}

const k = (rub: number) => formatDecimal(rub / 1000, 0)

/**
 * Гистограмма ожиданий кандидатов (Figma 33:305, 40:2). Бэкенд склеивает редкие ступени так, чтобы в каждой было
 * не меньше 3 человек (нельзя вычислить минимум отдельного кандидата), поэтому столбцы — это ступени, а не равные
 * интервалы: под каждым подписан его верх. Ступени, которые пересекаются с вашей вилкой, — фиолетовые.
 */
export function ReachHistogram({
  data,
  from,
  to,
  height = 96,
  className,
}: {
  data: HistogramData
  from?: number | null
  to?: number | null
  height?: number
  className?: string
}) {
  const { step, bins } = data
  if (bins.length === 0) return null
  const items = bins.map(([upper, count], i) => ({ upper, count, left: i === 0 ? upper - step : bins[i - 1][0] }))
  const max = Math.max(...items.map((b) => b.count), 1)
  const inRange = (b: { left: number; upper: number }) => from != null && to != null && b.upper > from && b.left < to

  return (
    <figure className={cn('m-0', className)}>
      <ol className="m-0 flex list-none items-end gap-1 p-0" style={{ height }}>
        {items.map((b) => (
          <li
            key={b.upper}
            title={`${k(b.left)}–${k(b.upper)} тыс. ₽: ${countWord(b.count, ['кандидат', 'кандидата', 'кандидатов'])}`}
            className="flex h-full min-w-0 flex-1 items-end"
          >
            <span
              className={cn('block w-full rounded-t-[4px]', inRange(b) ? 'bg-brand' : 'bg-bar')}
              style={{ height: `${Math.max(8, (b.count / max) * 100)}%` }}
            />
          </li>
        ))}
      </ol>
      <ol className="m-0 mt-2 flex list-none gap-1 p-0 t-small text-ink-3" aria-hidden="true">
        {items.map((b) => (
          <li key={b.upper} className="min-w-0 flex-1 truncate text-center">
            до {k(b.upper)}
          </li>
        ))}
      </ol>
      <figcaption className="sr-only">
        Ожидания кандидатов по ступеням, тысяч рублей в месяц:{' '}
        {items.map((b) => `до ${k(b.upper)} — ${countWord(b.count, ['кандидат', 'кандидата', 'кандидатов'])}`).join('; ')}
      </figcaption>
    </figure>
  )
}
