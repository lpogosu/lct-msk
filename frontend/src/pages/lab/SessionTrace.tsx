import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { errorMessage } from '@/api/errors'
import { useLabKit, useReconciliations } from '@/features/anticheat/api'
import { GRADE_RU, SESSION_STATUS_RU, useTrace, type Trace, type TraceStep } from '@/features/anticheat/labApi'
import { cn } from '@/lib/cn'
import { formatDecimal } from '@/lib/format'
import { Alert, Button, Panel } from '@/ui'
import { Chip, LabError, LabHead, LabSkeleton, Table, Td } from './parts'

const W = 880
const H = 260
const PAD = { l: 44, r: 16, t: 16, b: 28 }

/** Экран Л1 «Трасса сессии»: уровень и погрешность по шагам, порог, почему выбрано каждое задание. */
export default function SessionTrace() {
  const { id = '' } = useParams()
  const q = useTrace(id)
  return (
    <div className="flex flex-col gap-8">
      <nav aria-label="Навигация" className="t-small text-ink-3">
        <Link to="/lab/sessions" className="text-ink-2 underline underline-offset-4 hover:text-brand">
          Сессии
        </Link>{' '}
        / трасса
      </nav>
      {q.isPending ? <LabSkeleton rows={5} /> : q.isError ? <LabError error={q.error} onRetry={() => void q.refetch()} /> : <TraceBody t={q.data} />}
    </div>
  )
}

function TraceBody({ t }: { t: Trace }) {
  const steps = t.steps
  const done = steps.filter((s) => !s.is_pretest)
  const last = done[done.length - 1]
  return (
    <>
      <LabHead
        caption={`Сессия ${t.session_id.slice(0, 8)} · ${t.specialization}`}
        title="Трасса сессии"
        lead={
          <>
            Проверяли порог «{GRADE_RU[t.target_grade] ?? t.target_grade}». Заявлен {GRADE_RU[t.claimed_grade] ?? t.claimed_grade}, статус —{' '}
            {SESSION_STATUS_RU[t.status] ?? t.status}
            {t.decision ? `, решение ${t.decision}` : ''}.
            {last?.level != null && last.error != null && (
              <>
                {' '}
                К концу: {t.labels.level} {formatDecimal(last.level, 2)} ± {formatDecimal(last.error, 2)}.
              </>
            )}
          </>
        }
      />

      {steps.length === 0 ? (
        <Alert>В этой сессии пока нет ни одного ответа.</Alert>
      ) : (
        <Panel variant="panel" radius="lg" padding="md" className="flex flex-col gap-4">
          <TraceChart t={t} />
          <ul className="m-0 flex list-none flex-wrap gap-x-6 gap-y-2 p-0 t-small text-ink-2">
            <li className="flex items-center gap-2">
              <span className="block h-2.5 w-2.5 rounded-full bg-brand" /> верно
            </li>
            <li className="flex items-center gap-2">
              <span className="block h-2.5 w-2.5 rounded-full border-[1.5px] border-brand bg-white" /> неверно
            </li>
            <li className="flex items-center gap-2">
              <span className="block h-2.5 w-2.5 rounded-full bg-steel" /> пробное (в оценку не входит)
            </li>
            <li className="flex items-center gap-2">
              <span className="block h-0 w-5 border-t-[1.5px] border-dashed border-ink-3" /> порог, который проверяем
            </li>
            <li>вертикальная черта — {t.labels.error}</li>
          </ul>
        </Panel>
      )}

      <section className="flex flex-col gap-3" aria-labelledby="steps">
        <h2 id="steps" className="t-name text-ink">
          Шаги и причины выбора
        </h2>
        <Table head={['#', 'Задание', 'Ответ', t.labels.level, t.labels.error, 'Почему это задание']}>
          {steps.map((s) => (
            <StepRows key={s.seq} s={s} />
          ))}
        </Table>
      </section>

      {(t.flags?.length ?? 0) > 0 && (
        <section className="flex flex-col gap-3" aria-labelledby="flags">
          <h2 id="flags" className="t-name text-ink">
            Флаги целостности
          </h2>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {t.flags?.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center gap-3 rounded-panel bg-panel px-4 py-3">
                <Chip tone={f.severity === 'hard' ? 'danger' : 'neutral'}>{f.kind}</Chip>
                <span className="t-body text-ink">{f.message}</span>
                <span className="t-small text-ink-3">{f.status}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <AntiAssist sessionId={t.session_id} finished={['DECIDED', 'EXTENSION_OFFERED', 'FINAL', 'UNDER_REVIEW'].includes(t.status)} />
    </>
  )
}

function TraceChart({ t }: { t: Trace }) {
  const pts = t.steps.filter((s): s is TraceStep & { level: number; error: number } => s.level != null && s.error != null)
  if (pts.length === 0) return null
  const lo = Math.min(t.target_level, ...pts.map((p) => p.level - p.error))
  const hi = Math.max(t.target_level, ...pts.map((p) => p.level + p.error))
  const span = hi - lo || 1
  const x = (i: number) => PAD.l + (pts.length === 1 ? (W - PAD.l - PAD.r) / 2 : (i * (W - PAD.l - PAD.r)) / (pts.length - 1))
  const y = (v: number) => PAD.t + ((hi - v) / span) * (H - PAD.t - PAD.b)
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(p.level).toFixed(1)}`).join(' ')
  const ticks = [lo, (lo + hi) / 2, hi]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`${t.labels.level} и ${t.labels.error} по шагам теста`}>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className="stroke-hairline" strokeWidth="1" />
          <text x={PAD.l - 8} y={y(v) + 4} textAnchor="end" className="fill-ink-3 font-mono text-[11px]">
            {formatDecimal(v, 1)}
          </text>
        </g>
      ))}
      <line x1={PAD.l} x2={W - PAD.r} y1={y(t.target_level)} y2={y(t.target_level)} className="stroke-ink-3" strokeWidth="1.5" strokeDasharray="4 4" />
      <path d={path} fill="none" className="stroke-brand" strokeWidth="1.5" />
      {pts.map((p, i) => (
        <g key={p.seq}>
          <line x1={x(i)} x2={x(i)} y1={y(p.level + p.error)} y2={y(p.level - p.error)} className="stroke-heat-3" strokeWidth="2" strokeLinecap="round" />
          {p.is_pretest ? (
            <circle cx={x(i)} cy={y(p.level)} r="4.5" className="fill-steel" />
          ) : (
            <circle cx={x(i)} cy={y(p.level)} r="5" strokeWidth="1.5" className={cn('stroke-brand', p.correct ? 'fill-brand' : 'fill-white')} />
          )}
          <text x={x(i)} y={H - 8} textAnchor="middle" className="fill-ink-3 font-mono text-[11px]">
            {p.seq}
          </text>
        </g>
      ))}
    </svg>
  )
}

interface TopK {
  family_id?: string
  info?: number
  b?: number
}

function StepRows({ s }: { s: TraceStep }) {
  const [open, setOpen] = useState(false)
  const reason = s.reason as { text_ru?: string; top_k?: TopK[]; kind?: string; chosen?: string; deficit?: number; module?: string }
  return (
    <>
      <tr className="border-b border-hairline align-top">
        <Td mono>{s.seq}</Td>
        <Td className="max-w-[300px]">
          <span className="block t-label text-ink">{s.family_id}</span>
          <span className="block t-small text-ink-3">
            {s.module} · эксперты: {GRADE_RU[s.family_grade] ?? s.family_grade} · сложность {formatDecimal(s.difficulty, 2)}
            {s.variant_code ? ` · ${s.variant_code}` : ''}
          </span>
          {s.is_pretest && <Chip>пробное</Chip>}
        </Td>
        <Td>
          <span className={cn('t-label', s.correct ? 'text-ink' : 'text-ink-3')}>{s.timed_out ? 'таймаут' : s.correct ? 'верно' : 'неверно'}</span>
          <span className="block t-small text-ink-3">{s.rt_ms == null ? '—' : `${formatDecimal(s.rt_ms / 1000, 1)} с`}</span>
        </Td>
        <Td mono>{s.level == null ? '—' : formatDecimal(s.level, 2)}</Td>
        <Td mono>{s.error == null ? '—' : `±${formatDecimal(s.error, 2)}`}</Td>
        <Td>
          <Button variant="text" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? 'Скрыть' : 'Почему это задание'}
          </Button>
        </Td>
      </tr>
      {open && (
        <tr className="border-b border-hairline bg-panel/60">
          <td colSpan={6} className="px-4 py-4">
            <p className="t-body text-ink-2">{reason.text_ru ?? 'Причина выбора не записана.'}</p>
            {reason.top_k && reason.top_k.length > 0 && (
              <div className="mt-3 max-w-[560px]">
                <Table head={['Кандидат на выбор', 'Информативность', 'Сложность']}>
                  {reason.top_k.map((k) => (
                    <tr key={k.family_id} className="border-b border-hairline last:border-0">
                      <Td className={k.family_id === reason.chosen ? 't-label text-brand' : undefined}>{k.family_id}</Td>
                      <Td mono>{k.info == null ? '—' : formatDecimal(k.info, 3)}</Td>
                      <Td mono>{k.b == null ? '—' : formatDecimal(k.b, 2)}</Td>
                    </tr>
                  ))}
                </Table>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

/** Антиподсказчик по сессии: сверки с копией и комплект «Подтверждение за 5 минут». */
function AntiAssist({ sessionId, finished }: { sessionId: string; finished: boolean }) {
  const rec = useReconciliations('all', sessionId)
  const [showKit, setShowKit] = useState(false)
  const kit = useLabKit(sessionId, showKit && finished)
  const items = rec.data?.items ?? []
  return (
    <section className="flex flex-col gap-3" aria-labelledby="anti">
      <h2 id="anti" className="t-name text-ink">
        Антиподсказчик
      </h2>
      {rec.isPending ? (
        <LabSkeleton rows={1} />
      ) : items.length === 0 ? (
        <p className="t-body text-ink-2">Ответов, совпавших с копией из буфера обмена, в этой сессии нет.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {items.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 rounded-panel bg-panel px-4 py-3">
              <Link to={`/lab/reconciliations/${encodeURIComponent(r.id)}`} className="t-label text-ink no-underline hover:text-brand">
                Задание {r.seq}: {r.headline_ru}
              </Link>
              <Chip>{r.review_status_ru}</Chip>
            </li>
          ))}
        </ul>
      )}
      {finished && (
        <div className="flex flex-col gap-3">
          <div>
            <Button variant="outline" size="sm" onClick={() => setShowKit((v) => !v)} aria-expanded={showKit}>
              {showKit ? 'Скрыть «Подтверждение за 5 минут»' : 'Показать «Подтверждение за 5 минут»'}
            </Button>
          </div>
          {showKit && kit.isPending && <LabSkeleton rows={2} />}
          {showKit && kit.isError && <Alert tone="danger">{errorMessage(kit.error)}</Alert>}
          {showKit && kit.data && (
            <ol className="m-0 flex list-none flex-col gap-3 p-0">
              {kit.data.questions.map((x) => (
                <li key={x.id} className="rounded-panel bg-panel px-5 py-4">
                  <p className="t-small text-ink-3">
                    {x.id}
                    {x.selection_reason ? ` · выбрано: ${x.selection_reason}` : ''}
                    {x.label_ru ? ` · ${x.label_ru}` : ''}
                  </p>
                  <p className="t-body text-ink">{x.prompt_ru}</p>
                  {x.expected_answer && (
                    <p className="t-small text-ink-2">
                      Ожидаемый ответ: <span className="font-mono">{x.expected_answer}</span>
                    </p>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  )
}
