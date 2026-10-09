import { useState } from 'react'
import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'
import { Alert, Counters, Panel, Segmented } from '@/ui'
import type { CheckBriefRow, CheckSummary, MetricValue } from '../api'
import { fmtCi, fmtMetric, metricKeys, salaryText } from '../format'
import { Pill } from './common'

/** Метрики набора: наш подбор (пересчёт на стенде) и, если в файле есть места систем, сравнение с бейзлайнами. */
export function MetricsBlock({ summary }: { summary: CheckSummary }) {
  const m = summary.metrics
  if (!m) {
    return (
      <Alert title="Метрик нет: в наборе нет разметки">
        Добавьте таблицу разметки (vacancy_id, candidate_id, rel) — тогда появятся P@5, P@10 и nDCG@10. Топ по каждой вакансии
        можно посмотреть и без неё.
      </Alert>
    )
  }
  const keys = metricKeys(summary.ks, summary.ndcg_k)
  const systems = Object.keys(summary.system_labels).filter((s) => s in m.reported_systems)
  const extra = Object.keys(m.reported_systems).filter((s) => !systems.includes(s))
  const order = [...systems, ...extra]
  const p10 = `p@${Math.max(...summary.ks)}`
  const best = Math.max(m.ours[p10]?.mean ?? 0, ...order.map((s) => m.reported_systems[s]?.[p10]?.mean ?? 0))
  return (
    <section aria-labelledby="metrics-h" className="flex flex-col gap-5">
      <h2 id="metrics-h" className="t-title text-ink">
        Метрики набора
      </h2>
      <Counters
        items={[
          ...keys.map((k) => ({
            value: fmtMetric(m.ours[k.key]?.mean),
            label: `${k.label}${fmtCi(m.ours[k.key]) ? ` · ДИ ${fmtCi(m.ours[k.key])}` : ''}`,
            tone: k.key === p10 ? ('brand' as const) : ('default' as const),
          })),
          { value: formatInt(m.n_briefs), label: 'брифов с разметкой' },
        ]}
      />
      {order.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="t-label text-ink">Сравнение с бейзлайнами</h3>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-left t-body">
              <thead>
                <tr className="border-b border-hairline t-small text-ink-3">
                  <th scope="col" className="py-2.5 pr-3 font-medium">
                    Система
                  </th>
                  {keys.map((k) => (
                    <th key={k.key} scope="col" className="py-2.5 pr-3 font-medium">
                      {k.label} <span className="font-normal">[95% ДИ]</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <SystemRow
                  label="Наш подбор — пересчёт на этом стенде"
                  hint="Размеченные кандидаты каждого брифа ранжируются движком прямо сейчас"
                  values={m.ours}
                  keys={keys}
                  highlight={m.ours[p10]?.mean === best}
                  p10={p10}
                />
                {order.map((s) => (
                  <SystemRow
                    key={s}
                    label={summary.system_labels[s] ?? s}
                    values={m.reported_systems[s]}
                    keys={keys}
                    highlight={m.reported_systems[s]?.[p10]?.mean === best}
                    p10={p10}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <p className="t-small text-ink-3">
            Строки бейзлайнов считаются по местам систем, записанным в файле (поле ranks_top20), на той же разметке.
          </p>
        </div>
      )}
      {summary.notes.length > 0 && (
        <ul className="m-0 flex list-disc flex-col gap-1 pl-5 t-small text-ink-2">
          {summary.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function SystemRow({
  label,
  hint,
  values,
  keys,
  highlight,
  p10,
}: {
  label: string
  hint?: string
  values: Record<string, MetricValue> | undefined
  keys: { key: string; label: string }[]
  highlight: boolean
  p10: string
}) {
  return (
    <tr className="border-b border-hairline">
      <th scope="row" className="py-2.5 pr-3 text-left font-normal">
        <span className="t-body text-ink">{label}</span>
        {hint && <span className="block t-small text-ink-3">{hint}</span>}
      </th>
      {keys.map((k) => {
        const v = values?.[k.key]
        return (
          <td key={k.key} className="py-2.5 pr-3">
            <span className={cn('font-mono tabular-nums', highlight && k.key === p10 ? 'text-brand' : 'text-ink')}>
              {fmtMetric(v?.mean)}
            </span>
            {fmtCi(v) && <span className="ml-1.5 font-mono text-[12px] tabular-nums text-ink-3">[{fmtCi(v)}]</span>}
          </td>
        )
      })}
    </tr>
  )
}

type Order = 'id' | 'worst'

/** Брифы набора с P@k по каждому: клик — бриф, его разбор и топ для ручной проверки. */
export function BriefTable({ summary, onOpen }: { summary: CheckSummary; onOpen: (briefId: string) => void }) {
  const [order, setOrder] = useState<Order>('id')
  const keys = metricKeys(summary.ks, summary.ndcg_k)
  const p10 = `p@${Math.max(...summary.ks)}`
  const rows = [...summary.briefs]
  if (order === 'worst') rows.sort((a, b) => (a.metrics?.[p10] ?? 2) - (b.metrics?.[p10] ?? 2))
  const labeled = summary.n_labeled_briefs > 0
  return (
    <section aria-labelledby="briefs-h" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="briefs-h" className="t-title text-ink">
            Брифы ({formatInt(summary.briefs.length)})
          </h2>
          <p className="t-small text-ink-2">Нажмите на бриф: текст, как он разобран, топ с релевантностью по разметке.</p>
        </div>
        {labeled && (
          <Segmented
            aria-label="Порядок брифов"
            options={[
              { value: 'id', label: 'По номеру' },
              { value: 'worst', label: 'Сначала худшие' },
            ]}
            value={order}
            onChange={setOrder}
          />
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left t-body">
          <thead>
            <tr className="border-b border-hairline t-small text-ink-3">
              <th scope="col" className="py-2.5 pr-3 font-medium">
                Бриф
              </th>
              <th scope="col" className="py-2.5 pr-3 font-medium">
                Как разобран
              </th>
              <th scope="col" className="py-2.5 pr-3 font-medium">
                Вилка
              </th>
              {labeled && (
                <th scope="col" className="py-2.5 pr-3 font-medium">
                  Размечено
                </th>
              )}
              {labeled &&
                keys.map((k) => (
                  <th key={k.key} scope="col" className="py-2.5 pr-3 font-medium">
                    {k.label}
                  </th>
                ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <BriefRow key={b.brief_id} b={b} keys={labeled ? keys : []} labeled={labeled} p10={p10} onOpen={onOpen} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function BriefRow({
  b,
  keys,
  labeled,
  p10,
  onOpen,
}: {
  b: CheckBriefRow
  keys: { key: string; label: string }[]
  labeled: boolean
  p10: string
  onOpen: (id: string) => void
}) {
  const mismatch = b.gold_category && b.category && b.gold_category !== b.category
  return (
    <tr
      tabIndex={0}
      onClick={() => onOpen(b.brief_id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen(b.brief_id)
        }
      }}
      className="cursor-pointer border-b border-hairline align-top transition-colors hover:bg-panel focus-visible:bg-tint"
      aria-label={`Открыть бриф ${b.brief_id}`}
    >
      <td className="max-w-[420px] py-3 pr-3">
        <span className="font-mono text-[13px] text-brand">{b.brief_id}</span>
        <p className="line-clamp-2 t-small text-ink-2">{b.preview}</p>
      </td>
      <td className="py-3 pr-3">
        {b.parse_error ? (
          <p className="t-small text-danger-text">{b.parse_error}</p>
        ) : (
          <>
            <p className="t-small text-ink">{b.category}</p>
            {mismatch && (
              <p className="mt-0.5">
                <Pill tone="warn" title="Эталон разбора из набора">
                  эталон: {b.gold_category}
                </Pill>
              </p>
            )}
          </>
        )}
      </td>
      <td className="whitespace-nowrap py-3 pr-3 t-small text-ink-2">{salaryText(b.salary_from, b.salary_to, b.salary_net)}</td>
      {labeled && (
        <td className="whitespace-nowrap py-3 pr-3 t-small text-ink-2">
          {b.n_pairs ? `${formatInt(b.n_pairs)}, релевантных ${b.n_relevant ?? '—'}` : 'нет разметки'}
        </td>
      )}
      {keys.map((k) => (
        <td key={k.key} className="py-3 pr-3">
          <span className={cn('font-mono tabular-nums', k.key === p10 ? 't-body-strong text-ink' : 'text-ink-2')}>
            {fmtMetric(b.metrics?.[k.key])}
          </span>
        </td>
      ))}
    </tr>
  )
}

/** Шапка сводки: что за набор и сколько в нём всего. */
export function SummaryHead({ summary }: { summary: CheckSummary }) {
  return (
    <Panel variant="panel" radius="lg" padding="md" className="flex flex-col gap-1">
      <p className="t-body-strong text-ink">{summary.title}</p>
      <p className="t-small text-ink-2">
        Брифов {formatInt(summary.n_briefs)} (с разметкой {formatInt(summary.n_labeled_briefs)}) · кандидатов{' '}
        {formatInt(summary.n_candidates)}, из них без теста {formatInt(summary.n_unconfirmed)} · размеченных пар{' '}
        {formatInt(summary.n_pairs)}
      </p>
    </Panel>
  )
}
