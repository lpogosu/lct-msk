import type { CSSProperties } from 'react'
import { cn } from '@/lib/cn'

/**
 * «Трасса» — фирменный элемент: тонкая линия 1,5 px с узлами. Горизонтальная, растягивается по ширине.
 * Узлы позиционируются в долях длины (0…1), поэтому круги не искажаются при любой ширине.
 *
 *   <Trace nodes={[{ at: 0 }, { at: 1 }]} />                       — подчёркивание пункта меню, коннектор
 *   <Trace nodes={[{ at: 0, variant: 'open' }, { at: 1 }]} />      — версии подборки (первая → текущая)
 *   <Trace segments={[{ from: 0, to: 0.6 }, { from: 0.6, to: 1, dashed: true }]} … /> — «сделано / впереди»
 */
export type TraceNodeVariant = 'solid' | 'open' | 'current' | 'muted'

export interface TraceNode {
  /** Положение вдоль линии, 0…1 */
  at: number
  variant?: TraceNodeVariant
}

export interface TraceSegment {
  from: number
  to: number
  /** пунктир (steel) — «ещё не подтверждено / впереди» */
  dashed?: boolean
  /** цвет сплошного отрезка */
  tone?: 'brand' | 'steel' | 'ink3'
}

export interface TraceProps {
  nodes?: TraceNode[]
  /** По умолчанию — один сплошной brand-отрезок от первого до последнего узла */
  segments?: TraceSegment[]
  /** Высота контейнера, px (должна вмещать самый крупный узел) */
  height?: number
  /** Отступ линии от краёв, px: по радиусу крайнего узла, чтобы он не обрезался */
  inset?: number
  /** Нарисовать линию слева направо при появлении */
  animate?: boolean
  className?: string
  /** Для скринридера; без подписи трасса декоративна */
  label?: string
}

const NODE_PX: Record<TraceNodeVariant, number> = { solid: 5, open: 9, current: 12, muted: 6 }

const TONE: Record<NonNullable<TraceSegment['tone']>, string> = {
  brand: 'bg-brand',
  steel: 'bg-steel',
  ink3: 'bg-ink-3',
}

function TraceDot({ variant = 'solid' }: { variant?: TraceNodeVariant }) {
  const px = NODE_PX[variant]
  const style: CSSProperties = { width: px, height: px }
  switch (variant) {
    case 'open':
      return <span style={style} className="block rounded-full border-[1.5px] border-brand bg-white" />
    case 'current':
      return (
        <span style={style} className="flex items-center justify-center rounded-full border-2 border-brand bg-white">
          <span className="block h-[5px] w-[5px] rounded-full bg-brand" />
        </span>
      )
    case 'muted':
      return <span style={style} className="block rounded-full bg-steel" />
    default:
      return <span style={style} className="block rounded-full bg-brand" />
  }
}

export function Trace({
  nodes = [{ at: 0 }, { at: 1 }],
  segments,
  height = 14,
  inset = 3,
  animate,
  className,
  label,
}: TraceProps) {
  const sorted = [...nodes].sort((a, b) => a.at - b.at)
  const segs: TraceSegment[] =
    segments ?? (sorted.length > 1 ? [{ from: sorted[0].at, to: sorted[sorted.length - 1].at }] : [])

  return (
    <div
      className={cn('relative w-full', className)}
      style={{ height }}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {/* трек: узлы и отрезки считаются в процентах от его длины */}
      <div className="absolute inset-y-0" style={{ left: inset, right: inset }}>
        {segs.map((s, i) => (
          <span
            key={i}
            className={cn(
              'absolute top-1/2 h-[1.5px] -translate-y-1/2',
              s.dashed ? 'trace-dashed' : TONE[s.tone ?? 'brand'],
              animate && 'origin-left animate-trace-draw',
            )}
            style={{ left: `${s.from * 100}%`, width: `${(s.to - s.from) * 100}%` }}
          />
        ))}
        {sorted.map((n, i) => (
          <span
            key={i}
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${n.at * 100}%` }}
          >
            <TraceDot variant={n.variant} />
          </span>
        ))}
      </div>
    </div>
  )
}

/**
 * Прогресс теста: пройденные задания — залитые узлы на сплошной линии, текущее — кольцо,
 * дальше пунктир (Figma К4, 34:9). Число заданий заранее неизвестно, поэтому `estimatedTotal` — оценка.
 */
export function TraceProgress({
  done,
  estimatedTotal = 15,
  className,
  label,
}: {
  /** Сколько заданий уже пройдено (текущее — следующее по счёту) */
  done: number
  estimatedTotal?: number
  className?: string
  label?: string
}) {
  const total = Math.max(estimatedTotal, done + 1)
  const frac = (i: number) => i / total
  const nodes: TraceNode[] = [
    ...Array.from({ length: done }, (_, i) => ({ at: frac(i), variant: 'solid' as const })),
    { at: frac(done), variant: 'current' as const },
  ]
  return (
    <Trace
      className={className}
      height={14}
      inset={6}
      label={label ?? `Пройдено заданий: ${done}`}
      nodes={nodes}
      segments={[
        { from: 0, to: frac(done) },
        { from: frac(done), to: 1, dashed: true },
      ]}
    />
  )
}
