import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useCompany, useKanban, useNeeds, type NeedSummary } from '@/features/employer/api'
import { LoadError, PageHead, PageSkeleton, Tag } from '@/features/employer/components/common'
import { ProfileGate } from '@/features/employer/components/ProfileGate'
import { NEED_STATUS_LABEL } from '@/features/employer/dict'
import { formatDayMonth, formatRubRangeK, withSalaryBasis } from '@/lib/format'
import { Button, Counters, EmptyState } from '@/ui'

interface Funnel {
  sent: number
  viewed: number
  countered: number
  accepted: number
}

/** Р2 «Потребности»: список брифов со статусом, категорией, версией подборки, мини-воронкой и встречными условиями. */
export default function Needs() {
  const needs = useNeeds()
  const kanban = useKanban()
  const company = useCompany()

  // мини-воронка по брифам: считаем приглашения из канбана по need_id
  const funnels = useMemo(() => {
    // считает бэкенд по всем приглашениям: на странице канбана карточек может быть меньше
    const map = new Map<string, Funnel>()
    for (const f of kanban.data?.per_need ?? []) {
      map.set(f.need_id, { sent: f.sent, viewed: f.viewed, countered: f.countered, accepted: f.accepted })
    }
    return map
  }, [kanban.data])

  const items = needs.data ?? []
  const active = items.filter((n) => n.status === 'active').length
  const waiting = kanban.data?.columns.find((c) => c.status === 'countered')?.count ?? 0

  return (
    <div className="flex flex-col gap-8">
      <PageHead
        title="Потребности"
        lead={
          items.length > 0
            ? 'Опишите, кого ищете, — мы соберём подборку с объяснением по каждому кандидату.'
            : 'Опишите первую потребность — подборка соберётся за полминуты.'
        }
        actions={<Button to="/employer/needs/new">Описать потребность</Button>}
      />
      <ProfileGate />

      {needs.isPending ? (
        <PageSkeleton rows={3} />
      ) : needs.isError ? (
        <LoadError error={needs.error} onRetry={() => void needs.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title="Опишите первую потребность — подборка за 30 секунд"
          description="Напишите несколько предложений, как коллеге, и обязательно укажите вилку. Остальное мы разберём и покажем, как поняли."
          action={<Button to="/employer/needs/new">Описать потребность</Button>}
        />
      ) : (
        <>
          <Counters
            items={[
              { value: items.length, label: 'потребностей всего' },
              { value: active, label: 'ищем сейчас' },
              { value: waiting, label: 'встречных условий ждут ответа', tone: waiting > 0 ? 'brand' : 'default' },
              { value: company.data ? `${company.data.quota.remaining}/${company.data.quota.limit}` : '—', label: 'приглашений осталось на неделе' },
            ]}
          />
          <ul className="m-0 flex list-none flex-col border-t border-hairline p-0">
            {items.map((n) => (
              <NeedRow key={n.id} need={n} funnel={funnels.get(n.id)} />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function NeedRow({ need, funnel }: { need: NeedSummary; funnel?: Funnel }) {
  const href = need.current_version ? `/employer/needs/${need.id}` : `/employer/needs/${need.id}/edit`
  const closed = need.status === 'closed'
  return (
    <li className="grid gap-x-8 gap-y-3 border-b border-hairline py-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Link to={href} className="t-name text-ink no-underline hover:text-brand [overflow-wrap:anywhere]">
            {need.title || need.category}
          </Link>
          <Tag tone={need.status === 'active' ? 'brand' : 'default'}>{NEED_STATUS_LABEL[need.status] ?? need.status}</Tag>
          {need.is_internship && <Tag>стажировка</Tag>}
          {(funnel?.countered ?? 0) > 0 && (
            <span className="flex items-center gap-1.5 t-small text-danger-text">
              <span aria-hidden="true" className="h-2 w-2 rounded-full bg-danger" />
              встречное условие ждёт ответа
            </span>
          )}
        </div>
        <p className="mt-1 t-body text-ink-2">
          {need.category} · {withSalaryBasis(formatRubRangeK(need.salary_from, need.salary_to), need.salary_net)}
        </p>
        <p className="mt-0.5 t-small text-ink-3">
          Создана {formatDayMonth(need.created_at)}
          {need.current_version ? ` · версия подборки v${need.current_version}` : ' · подборка ещё не собрана'}
          {need.shortlist_count > 0 ? ` · в шортлисте ${need.shortlist_count}` : ''}
        </p>
      </div>
      <dl className="m-0 flex flex-wrap gap-x-6 gap-y-1">
        {funnel ? (
          <>
            <Stat label="отправлено" value={funnel.sent} />
            <Stat label="просмотрено" value={funnel.viewed} />
            <Stat label="встречных" value={funnel.countered} tone={funnel.countered > 0 ? 'brand' : undefined} />
            <Stat label="приняли" value={funnel.accepted} />
          </>
        ) : (
          <p className="t-small text-ink-3">Приглашений пока нет</p>
        )}
      </dl>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 lg:justify-end">
        {!closed && (
          <Button variant="outline" size="sm" to={href}>
            {need.current_version ? 'Открыть подборку' : 'Продолжить'}
          </Button>
        )}
        <Link to={`/employer/needs/${need.id}/edit`} className="t-label text-brand underline-offset-4 hover:underline">
          Изменить бриф
        </Link>
      </div>
    </li>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'brand' }) {
  return (
    <div className="flex flex-col-reverse">
      <dt className="t-small text-ink-3">{label}</dt>
      <dd className={tone === 'brand' ? 't-num-m m-0 text-brand' : 't-num-m m-0 text-ink'}>{value}</dd>
    </div>
  )
}
