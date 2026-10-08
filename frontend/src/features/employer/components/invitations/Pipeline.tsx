import { useMemo } from 'react'
import { cn } from '@/lib/cn'
import { Button, Trace, type TraceNode } from '@/ui'
import { useInvitation, type InvitationCard, type Kanban } from '../../api'
import { Tag } from '../common'

/** Колонки конвейера из статусов канбана (Figma 42:2): десять статусов бэкенда → пять этапов для глаза. */
export const STAGES = [
  { key: 'sent', label: 'Отправлено', statuses: ['sent'] },
  { key: 'viewed', label: 'Просмотрено', statuses: ['viewed'] },
  { key: 'countered', label: 'Ждут вашего ответа', statuses: ['countered'] },
  { key: 'accepted', label: 'Приняли — контакт открыт', statuses: ['accepted'] },
  {
    key: 'finished',
    label: 'Завершено',
    statuses: ['closed', 'declined', 'counter_declined', 'expired', 'withdrawn', 'consent_revoked'],
  },
] as const

export interface CardHandlers {
  onOpen: (card: InvitationCard) => void
  onWithdraw: (card: InvitationCard) => void
  onOutcome: (card: InvitationCard) => void
  onQuick: (card: InvitationCard, kind: 'contacted' | 'interview') => void
  busyId: string | null
}

export function Pipeline({ kanban, handlers }: { kanban: Kanban; handlers: CardHandlers }) {
  const cols = useMemo(
    () =>
      STAGES.map((s) => {
        const own = kanban.columns.filter((c) => (s.statuses as readonly string[]).includes(c.status))
        // count — по всем приглашениям (страница может быть неполной), items — загруженные карточки
        return { ...s, items: own.flatMap((c) => c.items), count: own.reduce((n, c) => n + c.count, 0) }
      }),
    [kanban],
  )
  const waiting = cols[2].count
  const nodes: TraceNode[] = cols.map((c, i) => ({
    at: i / (cols.length - 1),
    variant: c.key === 'countered' && waiting > 0 ? 'current' : 'solid',
  }))

  return (
    <div className="flex flex-col gap-4">
      <div className="hidden lg:block">
        <Trace nodes={nodes} height={16} inset={8} label="Конвейер приглашений: отправлено, просмотрено, ждут вашего ответа, приняли, завершено" />
      </div>
      <div className="grid gap-x-8 gap-y-10 md:grid-cols-2 lg:grid-cols-5">
        {cols.map((c) => (
          <section key={c.key} aria-label={c.label} className="min-w-0">
            <h3 className={cn('flex items-baseline gap-2 pb-3 t-label', c.key === 'countered' && c.count > 0 ? 'text-brand' : 'text-ink')}>
              {c.label}
              <span className="font-mono text-[13px] font-normal text-ink-3">{c.count}</span>
            </h3>
            {c.items.length === 0 ? (
              <p className="border-t border-hairline pt-3 t-small text-ink-3">Пока никого</p>
            ) : (
              <ul className="m-0 flex list-none flex-col p-0">
                {c.items.map((card) => (
                  <li key={card.id} className="border-t border-hairline py-3">
                    {c.key === 'accepted' ? (
                      <AcceptedCard card={card} h={handlers} />
                    ) : c.key === 'finished' ? (
                      <FinishedCard card={card} h={handlers} />
                    ) : (
                      <OpenCard card={card} h={handlers} stage={c.key} />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
    </div>
  )
}

function Name({ card, h }: { card: InvitationCard; h: CardHandlers }) {
  return (
    <button type="button" onClick={() => h.onOpen(card)} className="rounded-sm text-left t-label text-ink underline-offset-4 hover:text-brand hover:underline">
      {card.pseudonym}
    </button>
  )
}

function OpenCard({ card, h, stage }: { card: InvitationCard; h: CardHandlers; stage: string }) {
  const t = card.timers
  return (
    <div className="flex flex-col gap-0.5">
      <Name card={card} h={h} />
      <p className="t-small text-ink-2 [overflow-wrap:anywhere]">{card.title}</p>
      {stage === 'countered' ? (
        <p className="t-small text-ink-3">встречное условие{t.counter_respond_by_text ? `, ответить ${t.counter_respond_by_text}` : ''}</p>
      ) : (
        <p className="t-small text-ink-3">
          {card.sent_text}
          {t.respond_by_text ? `, ответ ${t.respond_by_text}` : ''}
        </p>
      )}
      {card.flags.includes('caution') && <Tag tone="danger">требует осторожности</Tag>}
      {card.actions.includes('withdraw') && (
        <button
          type="button"
          disabled={h.busyId === card.id}
          onClick={() => h.onWithdraw(card)}
          className="mt-1 self-start t-small text-brand underline-offset-4 hover:underline disabled:text-ink-3"
        >
          Отозвать
        </button>
      )}
    </div>
  )
}

/** Принято: имя и контакты кандидата (только при его согласии), срок «свяжитесь до…» — красным. */
function AcceptedCard({ card, h }: { card: InvitationCard; h: CardHandlers }) {
  const detail = useInvitation(card.id, card.contacts_open)
  const cand = (detail.data?.candidate ?? {}) as Record<string, string | null | undefined>
  const t = card.timers
  const busy = h.busyId === card.id
  return (
    <div className="flex flex-col gap-1">
      <button type="button" onClick={() => h.onOpen(card)} className="rounded-sm text-left t-label text-ink underline-offset-4 hover:text-brand hover:underline">
        {card.contacts_open ? (cand.full_name ?? card.pseudonym) : card.pseudonym}
      </button>
      {card.contacts_open && cand.full_name && <p className="t-small text-ink-3">была {card.pseudonym}</p>}
      {card.contacts_open &&
        (detail.isPending ? (
          <p className="t-small text-ink-3">Загружаем контакты…</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0 t-small">
            {cand.telegram && (
              <li>
                <a href={`https://t.me/${cand.telegram.replace(/^@/, '')}`} target="_blank" rel="noreferrer noopener" className="text-brand underline-offset-4 hover:underline">
                  Telegram {cand.telegram}
                </a>
              </li>
            )}
            {cand.email && (
              <li>
                <a href={`mailto:${cand.email}`} className="text-brand underline-offset-4 hover:underline [overflow-wrap:anywhere]">
                  {cand.email}
                </a>
              </li>
            )}
            {cand.phone && (
              <li>
                <a href={`tel:${cand.phone.replace(/[^\d+]/g, '')}`} className="text-brand underline-offset-4 hover:underline">
                  {cand.phone}
                </a>
              </li>
            )}
          </ul>
        ))}
      {!card.contacts_open && <p className="t-small text-ink-3">Контакты откроются после согласия кандидата</p>}
      {card.stage === 'interview' && <Tag tone="brand">собеседование</Tag>}
      {card.stage === 'offer' && <Tag tone="brand">оффер</Tag>}
      {!card.contacted && t.contact_due_text && (
        <p className={cn('t-small', card.contact_overdue ? 'font-medium text-danger-text' : 'text-danger-text')}>
          {card.contact_overdue ? 'Срок первого контакта вышел' : `Свяжитесь ${t.contact_due_text}`}
        </p>
      )}
      {card.contacted && <p className="t-small text-ink-3">Связались</p>}
      {card.disputed && <Tag tone="danger">кандидат сообщил: не связались</Tag>}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
        {card.actions.includes('contacted') && (
          <Button variant="text" disabled={busy} onClick={() => h.onQuick(card, 'contacted')}>
            Связались
          </Button>
        )}
        {card.actions.includes('interview') && card.stage !== 'interview' && (
          <Button variant="text" disabled={busy} onClick={() => h.onQuick(card, 'interview')}>
            Собеседование
          </Button>
        )}
        {card.actions.includes('outcome') && (
          <Button variant="text" disabled={busy} onClick={() => h.onOutcome(card)}>
            Итог
          </Button>
        )}
      </div>
    </div>
  )
}

function FinishedCard({ card, h }: { card: InvitationCard; h: CardHandlers }) {
  const line =
    card.outcome === 'hired'
      ? 'найм'
      : card.decline_reason_label
        ? `${card.status === 'counter_declined' ? 'вы отказали' : 'отказ'}: ${card.decline_reason_label.toLowerCase()}`
        : card.status_label.toLowerCase()
  return (
    <div className="flex flex-col gap-0.5">
      <Name card={card} h={h} />
      <p className="t-small text-ink-2 [overflow-wrap:anywhere]">{line}</p>
      {card.counter && card.status === 'counter_declined' && (
        <p className="t-small text-ink-3">просил {card.counter.summary}</p>
      )}
      {card.decline_comment && <p className="t-small text-ink-3 [overflow-wrap:anywhere]">«{card.decline_comment}»</p>}
      {line !== card.status_label.toLowerCase() && <p className="t-small text-ink-3">{card.status_label}</p>}
    </div>
  )
}
