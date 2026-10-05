import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Trace } from './Trace'

export interface TabOption<T extends string> {
  value: T
  label: ReactNode
  /** Число рядом: «Скрыто 4» */
  count?: number
}

export interface TabsProps<T extends string> {
  options: readonly TabOption<T>[]
  value: T
  onChange: (value: T) => void
  'aria-label': string
  className?: string
}

/** Текстовые вкладки с короткой трассой под активной — как пункты верхнего меню (Figma 27:3). */
export function Tabs<T extends string>({ options, value, onChange, className, ...aria }: TabsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!dir) return
    e.preventDefault()
    const next = (i + dir + options.length) % options.length
    onChange(options[next].value)
    refs.current[next]?.focus()
  }

  return (
    <div role="tablist" aria-label={aria['aria-label']} className={cn('flex flex-wrap gap-x-6 gap-y-2', className)}>
      {options.map((o, i) => {
        const active = o.value === value
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className="flex flex-col items-start rounded-sm"
          >
            <span className={cn('whitespace-nowrap', active ? 't-label text-ink' : 't-body text-ink-3 hover:text-ink')}>
              {o.label}
              {o.count != null && <span className="ml-1.5 font-mono tabular-nums text-ink-3">{o.count}</span>}
            </span>
            {active ? <Trace className="mt-1.5 w-10" height={6} inset={3} /> : <span className="mt-1.5 block h-1.5" />}
          </button>
        )
      })}
    </div>
  )
}
