import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'
import { GRADES, gradeByScore, SCORE_MAX, SCORE_MIN, scoreToRatio } from '@/lib/grades'

export interface RulerProps {
  /** Балл на шкале (S = 500 + 100·θ) */
  value?: number
  /** Доверительный интервал балла: толстый отрезок линейки */
  interval?: readonly [number, number]
  /** compact — 16 px в строках таблицы; full — с подписями грейдов (экран результата) */
  size?: 'compact' | 'full'
  min?: number
  max?: number
  /** Подписать значение над точкой (по умолчанию нет — число обычно стоит рядом отдельным счётчиком) */
  showValue?: boolean
  className?: string
}

/**
 * Линейка грейдов 300–600. Четыре полых узла — границы грейдов (Стажёр 300, Junior 400, Middle 500, Senior 600),
 * толстый фиолетовый отрезок — интервал уровня, крупная точка — оценка. Figma 27:83 (compact), 34:42 (full).
 * В full подпись грейда, в который попала оценка, выделена фиолетовым.
 */
export function Ruler({
  value,
  interval,
  size = 'compact',
  min = SCORE_MIN,
  max = SCORE_MAX,
  showValue = false,
  className,
}: RulerProps) {
  const full = size === 'full'
  const inset = full ? 10 : 4 // px от края: чтобы узлы и точка не обрезались
  const pos = (v: number) => `calc(${inset}px + (100% - ${inset * 2}px) * ${scoreToRatio(v, min, max)})`

  const grade = value != null ? gradeByScore(value) : null
  const aria =
    value != null
      ? `Уровень ${formatInt(Math.round(value))} из ${max}, грейд ${grade?.name}` +
        (interval ? `, интервал ${Math.round(interval[0])}–${Math.round(interval[1])}` : '')
      : 'Шкала уровня 300–600'

  // вертикальная раскладка full: [значение 24] [линия] [подписи грейдов]
  const lineY = full ? (showValue ? 40 : 10) : 8
  const labelsY = lineY + 16
  const height = full ? labelsY + 20 : 16
  const lineStyle = { top: lineY }

  return (
    <div role="img" aria-label={aria} className={cn('relative w-full', className)} style={{ height }}>
      {/* линия шкалы */}
      <span
        className={cn('absolute -translate-y-1/2 bg-steel', full ? 'h-[2px]' : 'h-px')}
        style={{ ...lineStyle, left: inset, right: inset }}
      />
      {/* интервал */}
      {interval && (
        <span
          className={cn('absolute -translate-y-1/2 rounded-full bg-brand', full ? 'h-[5px]' : 'h-[3px]')}
          style={{ ...lineStyle, left: pos(interval[0]), width: `calc(${pos(interval[1])} - ${pos(interval[0])})` }}
        />
      )}
      {/* границы грейдов */}
      {GRADES.map((g, i) => {
        if (g.cut < min || g.cut > max) return null
        const active = grade?.code === g.code
        const first = i === 0
        const last = i === GRADES.length - 1
        return (
          <span key={g.code}>
            <span
              className={cn(
                'absolute -translate-x-1/2 -translate-y-1/2 rounded-full border border-ink-3 bg-white',
                full ? 'h-2.5 w-2.5' : 'h-[6px] w-[6px]',
              )}
              style={{ ...lineStyle, left: pos(g.cut) }}
            />
            {full && (
              <span
                className={cn(
                  'absolute whitespace-nowrap t-small',
                  active ? 'font-medium text-brand' : 'text-ink-3',
                  !first && !last && '-translate-x-1/2',
                  last && '-translate-x-full',
                )}
                style={{ top: labelsY, left: first ? 0 : last ? '100%' : pos(g.cut) }}
              >
                {g.name}
              </span>
            )}
          </span>
        )
      })}
      {/* оценка */}
      {value != null && (
        <>
          <span
            className={cn(
              'absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-white bg-brand',
              full ? 'h-[18px] w-[18px] border-[3px]' : 'h-3 w-3 border-2',
            )}
            style={{ ...lineStyle, left: pos(value) }}
          />
          {showValue && (
            <span className="absolute top-0 -translate-x-1/2 t-num-m text-ink" style={{ left: pos(value) }}>
              {formatInt(Math.round(value))}
            </span>
          )}
        </>
      )}
    </div>
  )
}
