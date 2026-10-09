import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Alert } from '@/ui'

export function Pill({ children, tone = 'neutral', title }: { children: ReactNode; tone?: 'neutral' | 'brand' | 'danger' | 'warn'; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-pill px-2.5 py-0.5 t-small',
        tone === 'neutral' && 'bg-panel text-ink-2',
        tone === 'brand' && 'bg-tint text-brand',
        tone === 'danger' && 'bg-[#FFF1F3] text-danger-text',
        tone === 'warn' && 'bg-[#FFF7DB] text-[#7A5B00]',
      )}
    >
      {children}
    </span>
  )
}

/** Релевантность по разметке: «3 ✓» (релевантен, rel ≥ порога) или «1 ✗». */
export function RelBadge({ rel, relevant }: { rel: number | null | undefined; relevant: boolean | null | undefined }) {
  if (rel == null) return <span className="t-small text-ink-3">не размечен</span>
  return (
    <Pill tone={relevant ? 'brand' : 'neutral'} title={relevant ? 'Релевантен по разметке' : 'Не релевантен по разметке'}>
      <span className="font-mono tabular-nums">rel {rel}</span>
      <span aria-hidden="true" className="ml-1.5">
        {relevant ? '✓' : '✗'}
      </span>
      <span className="sr-only">{relevant ? ', релевантен' : ', не релевантен'}</span>
    </Pill>
  )
}

/** Плашка «как читать»: одинаковая во всех трёх сценариях. */
export function HowToRead({ relevantFrom = 2 }: { relevantFrom?: number }) {
  return (
    <Alert title="Как читать">
      <ul className="m-0 mt-1 flex list-disc flex-col gap-1 pl-5">
        <li>
          Разметка <span className="font-mono">rel</span> — от 0 до 3. Кандидат релевантен, если{' '}
          <span className="font-mono">rel ≥ {relevantFrom}</span>.
        </li>
        <li>
          P@10 — доля релевантных среди первых 10 мест (пустое место считается промахом); nDCG@10 учитывает ещё и порядок, и
          степень релевантности. В скобках — 95% доверительный интервал (бутстреп по брифам).
        </li>
        <li>
          Кандидаты без теста стоят ниже подтверждённых: категорию определяет тест, а не резюме. Для них Score считается по
          заявленной категории, вилке, формату и истории ФСП, если она есть; самоописание и заявленные навыки в Score не входят — это серые чипы «заявлено».
        </li>
      </ul>
    </Alert>
  )
}
