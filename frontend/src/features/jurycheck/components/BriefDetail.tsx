import type { ReactNode } from 'react'
import { errorMessage } from '@/api/errors'
import { formatDecimal, formatInt } from '@/lib/format'
import { Alert, Button, Counters, Panel, Skeleton } from '@/ui'
import type { CheckBrief } from '../api'
import { CandidateTable } from './CandidateTable'
import { fmtMetric, formatsText, salaryText } from '../format'
import { Pill } from './common'

type Parse = NonNullable<CheckBrief['brief']['parsed']>

/** Бриф для ручной проверки: текст, как разобран (и эталон набора), метрики брифа, топ с rel и ✓/✗. */
export function BriefDetail({
  briefId,
  detail,
  pending,
  error,
  onBack,
  onRetry,
}: {
  briefId: string
  detail: CheckBrief | undefined
  pending: boolean
  error: unknown
  onBack: () => void
  onRetry: () => void
}) {
  return (
    <section aria-labelledby="brief-h" className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-4">
        <Button variant="text" onClick={onBack}>
          ← Все брифы
        </Button>
        <h2 id="brief-h" className="t-title text-ink">
          Бриф <span className="font-mono text-brand">{briefId}</span>
        </h2>
      </div>
      {pending ? (
        <div className="flex flex-col gap-4" role="status" aria-label="Загрузка брифа">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-14 w-full rounded-panel" />
          ))}
        </div>
      ) : error || !detail ? (
        <Alert tone="danger" title="Не удалось открыть бриф">
          <span>{errorMessage(error)}</span>{' '}
          <Button variant="text" onClick={onRetry}>
            Повторить
          </Button>
        </Alert>
      ) : (
        <Body d={detail} />
      )}
    </section>
  )
}

function Body({ d }: { d: CheckBrief }) {
  const b = d.brief
  const labeled = d.mode === 'labeled'
  const top = d.items.length
  const hits = d.items.filter((i) => i.relevant).length
  return (
    <>
      <Panel variant="panel" radius="lg" padding="md" className="flex flex-col gap-2">
        <p className="t-small text-ink-3">Текст брифа</p>
        <p className="whitespace-pre-line t-body text-ink">{b.text}</p>
        {b.form && (b.form as Record<string, unknown>).salary_from != null && (
          <p className="t-small text-ink-2">
            Вилка из формы:{' '}
            {salaryText(
              Number((b.form as Record<string, unknown>).salary_from),
              Number((b.form as Record<string, unknown>).salary_to),
              (b.form as Record<string, unknown>).salary_net as boolean | null | undefined,
            )}
          </p>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <ParsePanel title="Как разобран продуктом" p={b.parsed} error={b.parse_error} gold={b.gold} />
        {b.gold && <ParsePanel title="Эталон разбора из набора" p={b.gold} />}
      </div>

      {labeled && d.metrics ? (
        <Counters
          items={[
            ...Object.entries(d.metrics).map(([k, v]) => ({
              value: fmtMetric(v),
              label: k.startsWith('p@') ? `P@${k.slice(2)} по брифу` : `nDCG@${k.split('@')[1]} по брифу`,
              tone: k === 'p@10' ? ('brand' as const) : ('default' as const),
            })),
            { value: `${formatInt(d.n_relevant)} из ${formatInt(d.n_labeled)}`, label: 'релевантных в разметке' },
          ]}
        />
      ) : null}

      <div className="flex flex-col gap-2">
        <h3 className="t-name text-ink">
          Топ-{top} {labeled ? `· релевантных ${hits} из ${top}` : ''}
        </h3>
        {d.notes.map((n) => (
          <p key={n} className="t-small text-ink-2">
            {n}
          </p>
        ))}
        {labeled && top >= 10 && (
          <p className="t-small text-ink-3">
            Проверка руками: P@10 = число ✓ в первых 10 строках / 10 = {formatDecimal(d.items.slice(0, 10).filter((i) => i.relevant).length / 10, 2)}.
          </p>
        )}
      </div>
      {d.items.length === 0 ? (
        <Alert title="Кандидатов нет">
          В рекомендованных категориях нет ни одного кандидата этой загрузки. Добавьте кандидатов нужной специализации или
          разметку для брифа.
        </Alert>
      ) : (
        <CandidateTable items={d.items} showRel={labeled} caption={`Топ кандидатов по брифу ${b.brief_id}`} />
      )}

      {d.relevant_below.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="t-label text-ink">Релевантные ниже топа</h3>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {d.relevant_below.map((r) => (
              <li key={r.candidate_id}>
                <Pill title={`${r.category} · Score ${formatDecimal(r.score, 1)}`}>
                  <span className="font-mono">{r.candidate_id}</span>
                  <span className="ml-1.5">место {r.rank} · rel {r.rel}</span>
                </Pill>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="t-small text-ink-3">Версия формулы: {d.scoring_config_version}</p>
    </>
  )
}

function ParsePanel({ title, p, error, gold }: { title: string; p: Parse | null; error?: string | null; gold?: Parse | null }) {
  if (!p) {
    return (
      <Panel variant="outline" radius="lg" padding="md" className="flex flex-col gap-2">
        <h3 className="t-label text-ink">{title}</h3>
        <p className="t-small text-danger-text">{error ?? 'Бриф не разобран'}</p>
      </Panel>
    )
  }
  const diff = (a: unknown, b: unknown) => gold != null && JSON.stringify(a) !== JSON.stringify(b)
  const row = (label: string, value: ReactNode, differs = false) => (
    <>
      <dt className="text-ink-3">{label}</dt>
      <dd className="m-0 text-ink">
        {value}
        {differs && (
          <span className="ml-2">
            <Pill tone="warn">не как в эталоне</Pill>
          </span>
        )}
      </dd>
    </>
  )
  const names = (xs: { name: string }[]) => (xs.length ? xs.map((x) => x.name).join(', ') : '—')
  return (
    <Panel variant="outline" radius="lg" padding="md" className="flex flex-col gap-3">
      <h3 className="t-label text-ink">{title}</h3>
      <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 t-small">
        {row('Категория', p.category, diff(p.category, gold?.category))}
        {row('Обязательно', names(p.must), diff(p.must.map((s) => s.id).sort(), gold?.must.map((s) => s.id).sort()))}
        {row('Желательно', names(p.nice))}
        {row('Вилка', salaryText(p.salary_from, p.salary_to, p.salary_net))}
        {row('Формат', `${formatsText(p.formats)}${p.city ? `, ${p.city}` : ''}`)}
      </dl>
      {p.source === 'gold' && <p className="t-small text-ink-3">Включён эталонный разбор: ранжирование идёт по нему.</p>}
    </Panel>
  )
}
