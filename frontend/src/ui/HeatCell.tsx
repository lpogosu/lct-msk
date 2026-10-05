import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'

/** Уровень насыщенности ячейки: 1 — меньше всего кандидатов, 5 — больше всего. 0 — пусто («—»). */
export type HeatLevel = 0 | 1 | 2 | 3 | 4 | 5

/**
 * Границы уровней по доле от максимума на карте: <5 % → 1, <18 % → 2, <35 % → 3, <70 % → 4, остальное → 5.
 * Подобраны по Figma 33:2 (96 → 5, 41 и 35 → 4, 31 → 3, 14 и 9 → 2, 3 → 1): малые категории не «слипаются» в один цвет.
 */
export const HEAT_BREAKS = [0.05, 0.18, 0.35, 0.7] as const

/** Переводит количество в уровень 1…5 относительно максимума по карте; 0 — «нет кандидатов». */
export function heatLevel(count: number, max: number, breaks: readonly number[] = HEAT_BREAKS): HeatLevel {
  if (count <= 0 || max <= 0) return 0
  const ratio = count / max
  let level = 1
  for (const b of breaks) if (ratio >= b) level += 1
  return Math.min(5, level) as HeatLevel
}

const SURFACE: Record<HeatLevel, string> = {
  0: 'bg-panel text-ink-3',
  1: 'bg-heat-1 text-ink',
  2: 'bg-heat-2 text-ink',
  3: 'bg-heat-3 text-ink',
  4: 'bg-heat-4 text-white',
  5: 'bg-heat-5 text-white',
}

export interface HeatCellProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /** Сколько кандидатов в категории «специализация × грейд» */
  count: number
  level: HeatLevel
  /** Доля кандидатов с достижениями ФСП, 0…1 — мини-трасса внизу ячейки */
  fspShare?: number
  selected?: boolean
  /** Полная подпись для скринридера: «Backend (Python), Middle: 38 кандидатов» */
  label: string
  children?: ReactNode
}

/**
 * Ячейка карты талантов (Figma 33:46): число — размер категории, цвет — насыщенность,
 * мини-трасса — доля с достижениями ФСП. Выбранная ячейка обведена чернилами 2 px.
 */
export function HeatCell({ count, level, fspShare, selected, label, className, ...rest }: HeatCellProps) {
  const dark = level >= 4
  const empty = level === 0
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={label}
      {...rest}
      className={cn(
        'flex h-[72px] w-full flex-col items-start gap-2 overflow-hidden rounded-cell border-2 px-3 py-2.5 text-left transition-colors',
        selected ? 'border-ink' : 'border-transparent',
        SURFACE[level],
        !empty && 'hover:brightness-[0.97]',
        className,
      )}
    >
      <span className="t-num-m">{empty ? '—' : formatInt(count)}</span>
      {!empty && fspShare != null && (
        <span className="relative block h-2 w-full max-w-[100px]" aria-hidden="true">
          <span className={cn('absolute inset-x-px top-1/2 h-[1.5px] -translate-y-1/2', dark ? 'bg-white/35' : 'bg-brand/20')} />
          <span
            className={cn('absolute left-px top-1/2 h-[1.5px] -translate-y-1/2', dark ? 'bg-white' : 'bg-brand')}
            style={{ width: `calc((100% - 2px) * ${Math.min(1, Math.max(0, fspShare))})` }}
          />
          <span
            className={cn('absolute top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full', dark ? 'bg-white' : 'bg-brand')}
            style={{ left: `calc(1px + (100% - 2px) * ${Math.min(1, Math.max(0, fspShare))})` }}
          />
        </span>
      )}
    </button>
  )
}

/** Легенда карты: «меньше □□□□□ больше кандидатов   ●— доля с достижениями ФСП» */
export function HeatLegend({ className }: { className?: string }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-x-6 gap-y-2 t-small text-ink-3', className)}>
      <span className="flex items-center gap-1">
        меньше
        {([1, 2, 3, 4, 5] as const).map((l) => (
          <span key={l} className={cn('h-2.5 w-4 rounded-[3px]', SURFACE[l].split(' ')[0])} aria-hidden="true" />
        ))}
        больше кандидатов
      </span>
      <span className="flex items-center gap-1.5">
        <span className="relative block h-2 w-7" aria-hidden="true">
          <span className="absolute left-px top-1/2 h-[1.5px] w-[21px] -translate-y-1/2 bg-brand" />
          <span className="absolute left-[22px] top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand" />
        </span>
        доля с достижениями ФСП
      </span>
    </div>
  )
}
