import { useMemo } from 'react'
import { hashString, seededRandom } from '@/lib/hash'
import { cn } from '@/lib/cn'

const GRID = 5
const MIN_DOTS = 8

/** Узор 5×5, симметричный по вертикали: из строки (псевдоним «К-4821») всегда получается один и тот же. */
export function identiconCells(seed: string): boolean[][] {
  const rnd = seededRandom(hashString(seed))
  const cols = Math.ceil(GRID / 2) // левая половина + центральный столбец, правая — зеркало
  const make = () =>
    Array.from({ length: GRID }, () => {
      const half = Array.from({ length: cols }, () => rnd() < 0.55)
      return [...half, ...half.slice(0, GRID - cols).reverse()]
    })
  let cells = make()
  // пустой или почти пустой узор выглядит как «нет данных» — перегенерируем детерминированно
  for (let guard = 0; guard < 8 && cells.flat().filter(Boolean).length < MIN_DOTS; guard++) cells = make()
  return cells
}

export interface IdenticonProps {
  /** Строка-источник: псевдоним, id */
  seed: string
  /** Сторона, px (в таблице 28) */
  size?: number
  className?: string
  /** Для скринридера; по умолчанию декоративный (имя уже написано рядом) */
  label?: string
}

export function Identicon({ seed, size = 28, className, label }: IdenticonProps) {
  const cells = useMemo(() => identiconCells(seed), [seed])
  const step = 4
  const r = 1.6
  const offset = (28 - step * (GRID - 1)) / 2 // 6: узор по центру плитки 28×28
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      className={cn('shrink-0', className)}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <rect width="28" height="28" rx="8" fill="#E6E2FF" />
      {cells.map((row, y) =>
        row.map((on, x) =>
          on ? <circle key={`${x}-${y}`} cx={offset + x * step} cy={offset + y * step} r={r} fill="#402FFF" /> : null,
        ),
      )}
    </svg>
  )
}
