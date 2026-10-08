import { CompanyTrustBadge } from '@/features/trust/CompanyTrustBadge'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Chip } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { salaryLine } from '@/features/candidate/format'
import { useInbox, type InboxOut } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { plural } from '@/lib/format'
import { Button, EmptyState, Segmented } from '@/ui'

type Filter = 'all' | 'wait' | 'accepted' | 'closed'
type Card = InboxOut['items'][number]

const FILTERS: { value: Filter; label: string; match: (c: Card) => boolean }[] = [
  { value: 'all', label: 'Все', match: () => true },
  { value: 'wait', label: 'Ждут ответа', match: (c) => ['sent', 'viewed', 'countered'].includes(c.status) },
  { value: 'accepted', label: 'Приняты', match: (c) => c.status === 'accepted' },
  { value: 'closed', label: 'Закрытые', match: (c) => !['sent', 'viewed', 'countered', 'accepted'].includes(c.status) },
]

/** К9 «Приглашения»: вилка первой строкой, компания и надёжность, срок ответа; фильтр по статусам. */
export default function CandidateInvitations() {
  const q = useInbox()
  const [filter, setFilter] = useState<Filter>('all')

  return (
    <>
      <QueryBoundary query={q}>
        {(inbox) => {
          const shown = inbox.items.filter(FILTERS.find((f) => f.value === filter)!.match)
          return (
            <div className="flex flex-col gap-5">
              <div>
                <h1 className="t-title text-brand">Приглашения</h1>
                {inbox.new_count > 0 && (
                  <p className="mt-2 t-body text-ink-2">
                    {inbox.new_count} {plural(inbox.new_count, ['новое', 'новых', 'новых'])} — откройте и ответьте до конца срока.
                  </p>
                )}
              </div>

              {inbox.items.length === 0 ? (
                <EmptyState
                  headingLevel={2}
                  title="Приглашений пока нет"
                  description="Они появятся, когда работодатель найдёт вас по категории. Пока можно откликнуться на вакансии или решить задачу — это поднимет вас в подборках."
                  action={
                    <div className="flex flex-col gap-2">
                      <Button to="/candidate/vacancies">Вакансии для меня</Button>
                      <Button to="/candidate/tasks" variant="outline">
                        Задачи
                      </Button>
                    </div>
                  }
                />
              ) : (
                <>
                  <Segmented
                    aria-label="Фильтр приглашений"
                    options={FILTERS.map((f) => ({ value: f.value, label: f.label }))}
                    value={filter}
                    onChange={setFilter}
                    className="flex-nowrap gap-2 overflow-x-auto pb-1 [&_button]:shrink-0 [&_button]:whitespace-nowrap [&_button]:px-3.5 [&_button]:py-2 [&_button]:t-small"
                  />
                  {shown.length === 0 ? (
                    <p className="py-6 text-center t-body text-ink-3">В этой группе приглашений нет.</p>
                  ) : (
                    <ul className="flex flex-col gap-3">
                      {shown.map((c) => (
                        <li key={c.id}>
                          <InvitationRow c={c} />
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
          )
        }}
      </QueryBoundary>
    </>
  )
}

function InvitationRow({ c }: { c: Card }) {
  const active = ['sent', 'viewed', 'countered'].includes(c.status)
  return (
    <Link
      to={`/candidate/invitations/${c.id}`}
      className={cn(
        'block rounded-panel-lg border p-4 text-ink no-underline transition-colors hover:bg-panel',
        c.is_new ? 'border-brand' : 'border-hairline',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="font-mono text-[22px] font-medium leading-7 tracking-tight text-brand">{salaryLine(c.salary.salary_from, c.salary.salary_to)}</p>
        {c.is_new && <Chip tone="brand">новое</Chip>}
      </div>
      <p className="mt-0.5 t-small text-ink-3">{c.salary.salary_type_label} в месяц</p>
      <p className="mt-3 t-label text-ink">{c.title}</p>
      <p className="mt-0.5 t-small text-ink-2">
        {c.company?.name ?? 'Компания'}
        {c.company ? ` · ${c.company.reliability}` : ''}
      </p>
      {c.company?.trust && c.company.trust.level !== 'none' && <CompanyTrustBadge trust={c.company.trust} className="mt-1.5" />}
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
        <Chip tone={c.status === 'accepted' ? 'tint' : 'panel'}>{c.status_label}</Chip>
        {active && c.timers.respond_by_text && <span className="t-small text-danger-text">ответить {c.timers.respond_by_text}</span>}
        {c.status === 'countered' && c.timers.counter_respond_by_text && (
          <span className="t-small text-ink-2">ответ компании {c.timers.counter_respond_by_text}</span>
        )}
        {c.caution && <Chip tone="danger">{c.caution}</Chip>}
        {c.sent_text && <span className="t-small text-ink-3">{c.sent_text}</span>}
      </div>
    </Link>
  )
}
