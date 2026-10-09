import { Fragment, useState } from 'react'
import { SCORE_PARTS } from '@/features/employer/dict'
import { cn } from '@/lib/cn'
import { formatDecimal, formatRub, salaryBasisLabel } from '@/lib/format'
import type { CheckRow, RankRow } from '../api'
import { FLAG_LABEL, formatsText } from '../format'
import { Pill, RelBadge } from './common'

type Row = RankRow & Partial<Pick<CheckRow, 'rel' | 'relevant' | 'profile'>>

const CHIP_STATE: Record<string, { label: string; tone: 'brand' | 'neutral' | 'warn' }> = {
  confirmed: { label: 'подтверждён', tone: 'brand' },
  weak: { label: 'проверен, слабо', tone: 'warn' },
  declared: { label: 'заявлено', tone: 'neutral' },
  no_data: { label: 'нет данных', tone: 'neutral' },
}

/**
 * Топ кандидатов: место, кандидат и категория, Score, «за»/«против» и (если набор размечен) релевантность rel с ✓/✗.
 * Строка раскрывается: вклады компонентов Score, навыки брифа у кандидата и — для своего набора — что о кандидате
 * известно (самоописание и заявленные навыки показываются, но в Score не входят).
 */
export function CandidateTable({ items, showRel, caption }: { items: Row[]; showRel: boolean; caption: string }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-left t-body">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-hairline t-small text-ink-3">
            <th scope="col" className="w-12 py-2.5 pr-3 font-medium">
              Место
            </th>
            <th scope="col" className="py-2.5 pr-3 font-medium">
              Кандидат
            </th>
            <th scope="col" className="w-16 py-2.5 pr-3 text-right font-medium">
              Score
            </th>
            <th scope="col" className="py-2.5 pr-3 font-medium">
              За и против
            </th>
            {showRel && (
              <th scope="col" className="w-28 py-2.5 pr-3 font-medium">
                Разметка
              </th>
            )}
            <th scope="col" className="w-24 py-2.5 font-medium">
              <span className="sr-only">Подробнее</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => {
            const key = `${it.rank}-${it.candidate_id}`
            const expanded = open === key
            const pros = it.reasons.filter((r) => r.kind === 'pro')
            const cons = it.reasons.filter((r) => r.kind === 'con')
            return (
              <Fragment key={key}>
                <tr className={cn('border-b border-hairline align-top', expanded && 'bg-panel', it.relevant && 'bg-tint/40')}>
                  <td className="py-3 pr-3">
                    <span className="t-num-m text-ink-2">{it.rank}</span>
                  </td>
                  <td className="py-3 pr-3">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="font-mono text-[13px] text-ink">{it.candidate_id}</span>
                      {it.group === 'unconfirmed' && <Pill tone="warn">без теста</Pill>}
                      {it.has_fsp && <Pill tone="brand">ФСП</Pill>}
                    </div>
                    <p className="t-small text-ink-2">{it.category}</p>
                  </td>
                  <td className="py-3 pr-3 text-right">
                    <span className="t-num-m text-ink">{formatDecimal(it.score, 1)}</span>
                  </td>
                  <td className="py-3 pr-3">
                    {pros.slice(0, 2).map((r, i) => (
                      <p key={i} className="line-clamp-2 t-small text-ink">
                        {r.text}
                      </p>
                    ))}
                    {pros.length === 0 && <p className="t-small text-ink-3">Данных для довода «за» мало</p>}
                    {cons.slice(0, 1).map((r, i) => (
                      <p key={i} className="mt-0.5 line-clamp-2 t-small text-danger-text">
                        Против: {r.text}
                      </p>
                    ))}
                  </td>
                  {showRel && (
                    <td className="py-3 pr-3">
                      <RelBadge rel={it.rel} relevant={it.relevant} />
                    </td>
                  )}
                  <td className="py-3">
                    <button
                      type="button"
                      className="t-small text-brand underline-offset-4 hover:underline"
                      aria-expanded={expanded}
                      onClick={() => setOpen(expanded ? null : key)}
                    >
                      {expanded ? 'Свернуть' : 'Подробнее'}
                    </button>
                  </td>
                </tr>
                {expanded && (
                  <tr className="border-b border-hairline bg-panel">
                    <td />
                    <td colSpan={showRel ? 5 : 4} className="pb-5 pr-3 pt-1">
                      <RowDetails row={it} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function RowDetails({ row }: { row: Row }) {
  const p = row.profile
  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <section className="flex flex-col gap-2">
        <h4 className="t-label text-ink">Из чего Score</h4>
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {SCORE_PARTS.map((part) => (
            <li key={part.key} className="flex justify-between gap-3 t-small" title={part.hint}>
              <span className="text-ink-2">{part.label}</span>
              <span className="font-mono tabular-nums text-ink">{formatDecimal(row.contributions[part.key] ?? 0, 1)}</span>
            </li>
          ))}
        </ul>
        <p className="t-small text-ink-3">Сумма вкладов = Score. {row.group_label}.</p>
        {row.flags.length > 0 && <p className="t-small text-ink-3">{row.flags.map((f) => FLAG_LABEL[f] ?? f).join(' · ')}</p>}
      </section>
      <section className="flex flex-col gap-2">
        <h4 className="t-label text-ink">Навыки брифа</h4>
        {row.chips.length === 0 ? (
          <p className="t-small text-ink-3">В брифе нет отдельных навыков: стек оценивается по модулям теста категории.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {row.chips.map((c) => {
              const st = CHIP_STATE[c.state] ?? { label: c.state, tone: 'neutral' as const }
              return (
                <li key={`${c.kind}-${c.name}`} className="flex flex-wrap items-center gap-2 t-small">
                  <span className="text-ink">{c.name}</span>
                  <span className="text-ink-3">{c.kind === 'must' ? 'обязательно' : 'желательно'}</span>
                  <Pill tone={st.tone} title={c.label}>
                    {st.label}
                  </Pill>
                </li>
              )
            })}
          </ul>
        )}
        {row.reasons.length > 2 && (
          <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
            {row.reasons.slice(2).map((r, i) => (
              <li key={i} className={cn('t-small', r.kind === 'con' ? 'text-danger-text' : 'text-ink-2')}>
                {r.kind === 'con' ? 'Против: ' : 'За: '}
                {r.text}
              </li>
            ))}
          </ul>
        )}
      </section>
      {p && (
        <section className="flex flex-col gap-2">
          <h4 className="t-label text-ink">Кандидат в наборе</h4>
          <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 t-small">
            <dt className="text-ink-3">Категория</dt>
            <dd className="m-0 text-ink">{p.confirmed ? 'подтверждена тестом' : 'по опросу, тестом не подтверждена'}</dd>
            {p.experience_months != null && (
              <>
                <dt className="text-ink-3">Стаж</dt>
                <dd className="m-0 text-ink">{p.experience_months} мес.</dd>
              </>
            )}
            {p.salary_min != null && (
              <>
                <dt className="text-ink-3">Ожидания</dt>
                <dd className="m-0 text-ink">
                  от {formatRub(p.salary_min)} {salaryBasisLabel(p.salary_net)}
                </dd>
              </>
            )}
            <dt className="text-ink-3">Формат</dt>
            <dd className="m-0 text-ink">
              {formatsText(p.formats)}
              {p.city ? `, ${p.city}` : ''}
            </dd>
            {p.fsp_line && (
              <>
                <dt className="text-ink-3">ФСП</dt>
                <dd className="m-0 text-ink">{p.fsp_line}</dd>
              </>
            )}
          </dl>
          {p.declared_skills.length > 0 && (
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Заявленные навыки (в Score не входят)">
              {p.declared_skills.map((s) => (
                <Pill key={s} title="Заявлено: в Score не входит">
                  {s}
                </Pill>
              ))}
            </div>
          )}
          {p.self_description && (
            <p className="t-small text-ink-2">
              «{p.self_description}» <span className="text-ink-3">— самоописание, в Score не входит</span>
            </p>
          )}
        </section>
      )}
    </div>
  )
}
