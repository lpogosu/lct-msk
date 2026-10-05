import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  disabled?: boolean
}

export interface SegmentedProps<T extends string> {
  options: readonly SegmentedOption<T>[]
  value: T | null
  onChange: (value: T) => void
  /** Подпись группы для скринридера (обязательна) */
  'aria-label': string
  /** Растянуть на всю ширину, варианты поровну */
  block?: boolean
  className?: string
}

/**
 * Выбор одного из вариантов «таблетками» (Figma 40:40: «Middle · Senior · Обоих»).
 * Это radiogroup: стрелки переключают вариант, Tab входит в группу один раз.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  block,
  className,
  ...aria
}: SegmentedProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const enabled = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0)
  const activeIndex = options.findIndex((o) => o.value === value)
  const tabbable = activeIndex >= 0 ? activeIndex : (enabled[0] ?? 0)

  const move = (from: number, dir: 1 | -1) => {
    const pos = enabled.indexOf(from)
    const next = enabled[(pos + dir + enabled.length) % enabled.length]
    if (next === undefined) return
    onChange(options[next].value)
    refs.current[next]?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      move(i, 1)
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      move(i, -1)
    }
  }

  return (
    <div
      role="radiogroup"
      aria-label={aria['aria-label']}
      className={cn('flex flex-wrap gap-2.5', block && 'w-full', className)}
    >
      {options.map((o, i) => {
        const selected = o.value === value
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={o.disabled}
            tabIndex={i === tabbable ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              'rounded-pill border px-[18px] py-2.5 t-label transition-colors disabled:cursor-not-allowed disabled:opacity-50',
              block && 'flex-1',
              selected
                ? 'border-brand bg-brand text-white'
                : 'border-steel bg-white text-ink hover:border-brand hover:text-brand',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
