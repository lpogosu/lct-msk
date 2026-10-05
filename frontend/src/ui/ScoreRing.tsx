import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'

export interface ScoreRingProps {
  /** Значение 0…max */
  value: number
  max?: number
  /** Диаметр, px (в Figma — 88) */
  size?: number
  /** Содержимое центра; по умолчанию — число value */
  children?: ReactNode
  className?: string
  /** Подпись для скринридера, например «Score 83 из 100» */
  label?: string
}

/**
 * Кольцо Score: серая дорожка, фиолетовая дуга по часовой от 12 часов и узел на её конце —
 * то же «трассировка с узлом», свёрнутое в круг. Figma 32:91.
 */
export function ScoreRing({ value, max = 100, size = 88, children, className, label }: ScoreRingProps) {
  const stroke = (3 * size) / 88
  const r = size / 2 - (4 * size) / 88 // 40 при size=88: отступ 4 px от края под узел
  const c = size / 2
  const ratio = Math.min(1, Math.max(0, value / max))
  const circumference = 2 * Math.PI * r
  const angle = ratio * 2 * Math.PI - Math.PI / 2 // старт — 12 часов
  const nodeX = c + r * Math.cos(angle)
  const nodeY = c + r * Math.sin(angle)

  return (
    <div
      role="img"
      aria-label={label ?? `Score ${formatInt(value)} из ${formatInt(max)}`}
      className={cn('relative shrink-0', className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} fill="none" aria-hidden="true">
        <circle cx={c} cy={c} r={r} stroke="#E4E4E7" strokeWidth={stroke} />
        {ratio > 0 && (
          <circle
            cx={c}
            cy={c}
            r={r}
            stroke="#402FFF"
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${circumference * ratio} ${circumference}`}
            transform={`rotate(-90 ${c} ${c})`}
          />
        )}
        {ratio > 0 && <circle cx={nodeX} cy={nodeY} r={(4 * size) / 88} fill="#402FFF" />}
      </svg>
      <div
        className="absolute inset-0 flex items-center justify-center font-mono font-medium text-brand"
        style={{ fontSize: (28 * size) / 88, letterSpacing: '-0.5px' }}
      >
        {children ?? formatInt(value)}
      </div>
    </div>
  )
}
