import { formatDate, formatRub } from '@/lib/format'
import { Button, Panel, Trace } from '@/ui'
import type { InvitationCard, InvitationDetail } from '../../api'
import { FORMAT_LABEL } from '../../dict'

function CompareRow({ label, yours, theirs, verdict, same }: { label: string; yours: string; theirs: string; verdict: string; same: boolean }) {
  return (
    <div className="grid grid-cols-[72px_minmax(0,1fr)] items-center gap-x-4 gap-y-1 sm:grid-cols-[88px_minmax(0,170px)_100px_minmax(0,150px)_minmax(0,1fr)]">
      <span className="t-small text-ink-3">{label}</span>
      <span className="t-body text-ink">{yours}</span>
      <Trace
        className="hidden sm:block"
        nodes={[{ at: 0 }, { at: 1, variant: 'open' }]}
        segments={same ? undefined : [{ from: 0, to: 1, dashed: true }]}
        height={14}
        inset={4}
      />
      <span className="t-body-strong text-ink">{theirs}</span>
      <span className="col-start-2 t-small text-brand sm:col-start-auto">{verdict}</span>
    </div>
  )
}

/**
 * Встречное условие «Да, если…» наверху страницы (Figma 42:2): что было в приглашении, что просит кандидат и
 * чем отличается. «Согласиться» фиксирует условия новой версией v2 и открывает контакты, «Отказать» требует причину.
 */
export function CounterOfferCard({
  card,
  detail,
  onAccept,
  onDecline,
  busy,
}: {
  card: InvitationCard
  detail?: InvitationDetail
  onAccept: () => void
  onDecline: () => void
  busy: boolean
}) {
  const c = card.counter
  if (!c) return null
  const terms = detail?.terms[0]
  const canAccept = card.actions.includes('counter/accept')
  const canDecline = card.actions.includes('counter/decline')
  const timer = card.timers.counter_respond_by_text

  const rows: { label: string; yours: string; theirs: string; verdict: string; same: boolean }[] = []
  if (c.salary_from != null) {
    rows.push({
      label: 'Зарплата',
      yours: card.salary.text,
      theirs: `от ${formatRub(c.salary_from)}`,
      verdict: c.within_range ? 'внутри вашей вилки' : 'выше вашей вилки',
      same: c.within_range,
    })
  }
  if (c.format) {
    const mine = terms?.format_label ?? (terms?.format ? FORMAT_LABEL[terms.format] : null) ?? 'не указан'
    const theirs = FORMAT_LABEL[c.format] ?? c.format
    rows.push({ label: 'Формат', yours: mine, theirs, verdict: mine === theirs ? 'совпадает' : 'отличается', same: mine === theirs })
  }
  if (c.start_date) {
    const mine = terms?.start_date ? formatDate(terms.start_date) : 'не указана'
    rows.push({
      label: 'Выход',
      yours: mine,
      theirs: formatDate(c.start_date),
      verdict: terms?.start_date === c.start_date ? 'совпадает' : 'отличается',
      same: terms?.start_date === c.start_date,
    })
  }

  return (
    <Panel variant="tint" radius="xl" padding="lg" className="flex flex-col gap-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="t-body text-ink-2">
          <span className="t-name mr-2 text-ink">{card.pseudonym}</span>
          ответил «да, если…» на «{card.title}»
        </p>
        {timer && <p className="t-small text-danger-text">Ответить {timer}</p>}
      </div>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
        <div className="flex min-w-0 flex-col gap-4">
          {rows.map((r) => (
            <CompareRow key={r.label} {...r} />
          ))}
          {c.comment && <p className="t-body text-ink-2 [overflow-wrap:anywhere]">«{c.comment}»</p>}
          <p className="t-body text-ink-2">
            Если согласитесь, условия зафиксируются новой версией
            {c.auto_disclose
              ? ', а имя и контакты откроются сразу.'
              : '. Контакты откроются, когда кандидат подтвердит согласие — он отключил автораскрытие.'}
          </p>
        </div>
        <div className="flex flex-col gap-3 lg:w-[290px]">
          {canAccept && (
            <Button block loading={busy} onClick={onAccept}>
              {c.auto_disclose ? 'Согласиться и открыть контакты' : 'Согласиться с условиями'}
            </Button>
          )}
          {canDecline && (
            <Button block variant="outline" disabled={busy} onClick={onDecline}>
              Отказать с причиной
            </Button>
          )}
        </div>
      </div>
    </Panel>
  )
}
