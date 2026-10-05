import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export type JourneyState = 'done' | 'current' | 'todo' | 'optional'

export interface JourneyStep {
  key: string
  title: string
  /** Одна строка о состоянии шага: «Backend (Python) · Middle» */
  detail?: ReactNode
  state: JourneyState
  /** Кнопка или ссылка действия — только у текущего шага (остальные открываются по ссылке в detail) */
  action?: ReactNode
}

/**
 * Вертикальная «трасса» пути кандидата (Figma К1): пройденные шаги — залитые узлы на сплошной линии,
 * текущий — кольцо с точкой, дальше — серые узлы и пунктир. Необязательные шаги (ФСП, профиль) — пустые кольца.
 */
export function Journey({ steps, className }: { steps: JourneyStep[]; className?: string }) {
  return (
    <ol className={cn('m-0 list-none p-0', className)} aria-label="Ваш путь">
      {steps.map((s, i) => {
        const last = i === steps.length - 1
        const lineSolid = s.state === 'done' && steps[i + 1]?.state !== 'todo' && steps[i + 1]?.state !== 'optional'
        return (
          <li key={s.key} className="relative flex gap-4 pb-6 last:pb-0" aria-current={s.state === 'current' ? 'step' : undefined}>
            <div className="relative flex w-5 shrink-0 justify-center">
              {!last && (
                <span
                  aria-hidden="true"
                  className={cn(
                    'absolute left-1/2 top-5 -bottom-6 -translate-x-1/2',
                    lineSolid ? 'w-[1.5px] bg-brand' : 'w-0 border-l-[1.5px] border-dashed border-steel',
                  )}
                />
              )}
              <span className="relative mt-[3px] flex h-4 w-4 items-center justify-center bg-white">
                <Node state={s.state} />
              </span>
            </div>
            <div className="min-w-0 flex-1">
              <p
                className={cn(
                  't-name',
                  s.state === 'done' && 'text-ink',
                  s.state === 'current' && 'text-brand',
                  (s.state === 'todo' || s.state === 'optional') && 'text-ink-2',
                )}
              >
                {s.title}
                <span className="sr-only">
                  {s.state === 'done' ? ' — выполнено' : s.state === 'current' ? ' — сейчас' : s.state === 'optional' ? ' — по желанию' : ' — впереди'}
                </span>
              </p>
              {s.detail && <div className="mt-0.5 t-small text-ink-3">{s.detail}</div>}
              {s.action && <div className="mt-3">{s.action}</div>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function Node({ state }: { state: JourneyState }) {
  switch (state) {
    case 'done':
      return <span className="block h-3 w-3 rounded-full bg-brand" />
    case 'current':
      return (
        <span className="flex h-4 w-4 items-center justify-center rounded-full border-2 border-brand bg-white">
          <span className="block h-[5px] w-[5px] rounded-full bg-brand" />
        </span>
      )
    case 'optional':
      return <span className="block h-3 w-3 rounded-full border-[1.5px] border-ink-3 bg-white" />
    default:
      return <span className="block h-2.5 w-2.5 rounded-full bg-steel" />
  }
}
