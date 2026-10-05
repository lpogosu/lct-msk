import type { ReactNode } from 'react'
import { Identicon, Panel, Ruler, TrustNode } from '@/ui'
import { Chip } from './Bits'

export interface BlindCardData {
  pseudonym: string
  /** «Backend · Middle» */
  category?: string | null
  /** Строка-бейдж: «Тест: верхние 10% категории · подтверждён 3 недели назад» */
  badge?: string | null
  interval?: readonly [number, number] | null
  modules?: { name: string; symbol?: string }[]
  declared?: string[]
  freshness?: string | null
  trust?: string | null
  fsp?: string[]
  region?: string | null
  salarySignal?: string | null
  how?: string | null
  /** «Чего работодатель не видит» */
  hidden?: string | null
}

/**
 * Слепая карточка — то, что работодатель видит до взаимного «да» (концепция §4.5): псевдоним, категория, полоса
 * теста без точного балла, подтверждённые модули, заявленное серым, обобщённое ФСП. Имени и контактов здесь нет и быть не может.
 */
export function BlindCard({ data, footer }: { data: BlindCardData; footer?: ReactNode }) {
  const interval = data.interval && data.interval.length === 2 ? data.interval : null
  return (
    <Panel variant="outline" radius="lg" padding="sm" className="flex flex-col gap-3" aria-label="Слепая карточка кандидата">
      <div className="flex items-center gap-3">
        <Identicon seed={data.pseudonym} size={36} />
        <div className="min-w-0">
          <p className="font-mono text-[15px] font-medium text-ink">{data.pseudonym}</p>
          {data.category && <p className="t-small text-ink-2">{data.category}</p>}
        </div>
      </div>

      {interval && <Ruler size="compact" interval={interval} value={Math.round((interval[0] + interval[1]) / 2)} className="max-w-[160px]" />}
      {data.badge && <p className="t-small text-ink-2">{data.badge}</p>}
      {data.how && <p className="t-small text-ink-3">{data.how}</p>}

      {data.modules && data.modules.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {data.modules.map((m) => (
            <li key={m.name}>
              <span className="inline-flex items-center gap-1.5 rounded-pill bg-panel px-2.5 py-1 t-small text-ink">
                <TrustNode kind="test" surface="#F6F6F8" />
                {m.name}
                {m.symbol ? <span className="text-brand">{m.symbol}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      )}

      {data.declared && data.declared.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Заявлено, не проверено">
          {data.declared.map((d) => (
            <li key={d}>
              <span className="inline-flex items-center gap-1.5 rounded-pill border border-dashed border-steel px-2.5 py-1 t-small text-ink-3">
                <TrustNode kind="claimed" />
                {d}
              </span>
            </li>
          ))}
        </ul>
      )}

      {(data.fsp && data.fsp.length > 0) || data.freshness || data.trust || data.region || data.salarySignal ? (
        <div className="flex flex-wrap gap-1.5">
          {data.fsp?.map((f) => (
            <Chip key={f} tone="tint">
              <TrustNode kind="fsp" surface="#F3F1FF" />
              {f}
            </Chip>
          ))}
          {data.freshness && <Chip tone="panel">{data.freshness}</Chip>}
          {data.trust && <Chip tone="panel">доверие: {data.trust}</Chip>}
          {data.region && <Chip tone="panel">{data.region}</Chip>}
          {data.salarySignal && <Chip tone="panel">{data.salarySignal}</Chip>}
        </div>
      ) : null}

      {data.hidden && <p className="border-t border-hairline pt-3 t-small text-ink-3">{data.hidden}</p>}
      {footer}
    </Panel>
  )
}
