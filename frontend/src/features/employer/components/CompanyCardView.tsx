import type { Schemas } from '@/api/types'
import { formatDecimal } from '@/lib/format'
import { Panel, Trace } from '@/ui'
import { Label, Tag } from './common'

export interface CompanyCardData {
  name: string
  field?: string | null
  description?: string | null
  website?: string | null
  personal_email_label?: string | null
  contact_methods: { method: string; label: string; value: string }[]
  reliability: Schemas['ReliabilityOut']
}

/** Надёжность компании с разложением — единица доверия кандидата к приглашению (концепция §4.3, §4.5). */
export function ReliabilityBlock({ reliability }: { reliability: Schemas['ReliabilityOut'] }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-3">
        <span className="t-num-m text-ink">{formatDecimal(reliability.r, 2)}</span>
        <span className="t-label text-ink-2">надёжность компании</span>
        {reliability.discrepancy_badge && <Tag tone="danger">{reliability.discrepancy_badge}</Tag>}
      </div>
      {reliability.enough_data ? (
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {reliability.breakdown.map((line) => (
            <li key={line} className="t-small text-ink-2">
              {line}
            </li>
          ))}
        </ul>
      ) : (
        <p className="t-small text-ink-3">
          Мало данных: надёжность начнёт считаться после первых ответов кандидатам. Пока показываем стартовое значение.
        </p>
      )}
    </div>
  )
}

/**
 * «Так видит кандидат»: карточка компании, которая стоит в каждом приглашении (Р1, Р7).
 * Это именно то, что кандидат видит до своего решения: название, описание, способы связи, надёжность.
 */
export function CompanyCardView({ card, title = 'Так видит кандидат' }: { card: CompanyCardData; title?: string }) {
  return (
    <Panel variant="tint" radius="xl" padding="md" aria-label={title} className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="t-body-strong text-ink">{title}</h2>
        <Trace className="w-12" height={8} inset={3} />
      </div>
      <div className="flex flex-col gap-1">
        <p className="t-name text-ink [overflow-wrap:anywhere]">{card.name}</p>
        {card.field && <p className="t-small text-ink-2">{card.field}</p>}
        {card.personal_email_label && (
          <p className="mt-1">
            <Tag>{card.personal_email_label}</Tag>
          </p>
        )}
      </div>
      {card.description ? (
        <p className="t-body text-ink-2 [overflow-wrap:anywhere]">{card.description}</p>
      ) : (
        <p className="t-body text-ink-3">Описание компании пока пустое — кандидат увидит только название.</p>
      )}
      <div className="flex flex-col gap-1">
        <Label>Как с вами связаться после «да»</Label>
        {card.contact_methods.length > 0 ? (
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {card.contact_methods.map((m) => (
              <li key={m.method} className="t-body text-ink">
                <span className="text-ink-3">{m.label}: </span>
                <span className="[overflow-wrap:anywhere]">{m.value}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="t-body text-ink-3">Способ связи не указан</p>
        )}
        {card.website && <p className="t-small text-ink-2 [overflow-wrap:anywhere]">{card.website}</p>}
      </div>
      <div className="border-t border-brand/15 pt-4">
        <ReliabilityBlock reliability={card.reliability} />
      </div>
    </Panel>
  )
}
