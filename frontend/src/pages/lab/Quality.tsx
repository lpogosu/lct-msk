import { useState } from 'react'
import { useLabQuality } from '@/features/anticheat/labApi'
import { formatDecimal, formatInt } from '@/lib/format'
import { Alert, Counters, EmptyState, Panel, Segmented } from '@/ui'
import { Chip, LabError, LabHead, LabSkeleton, Table, Td } from './parts'

type View = 'dif' | 'drift' | 'exposure'
type QualityData = NonNullable<ReturnType<typeof useLabQuality>['data']>

const VIEWS = [
  { value: 'dif', label: 'DIF по группам' },
  { value: 'drift', label: 'Дрейф сложности' },
  { value: 'exposure', label: 'Экспозиция' },
] as const

const GROUP_RU: Record<string, string> = {
  fsp: 'Без истории ФСП',
  region: 'Регионы',
  a11y: 'Режим доступности',
  express: 'Экспресс-версия',
}

interface DifRow {
  group: string
  family_id: string
  delta: number | null
  dif_class: string
  p_ref: number | null
  p_focal: number | null
  planted_shift: number | null
}
interface Planted {
  group: string
  family_id: string
  shift: number
  dif_class?: string
  delta?: number | null
}
interface FamExp {
  family_id: string
  baseline: number
  sh: number
}
interface Counts {
  tested: number
  A: number
  B: number
  C: number
}
interface Synthetic {
  dif?: {
    n_sessions: number
    rows: DifRow[]
    planted: Planted[]
    real_by_group: Record<string, Counts>
    real_total: Counts
    real_share_A: number | null
  }
  drift?: {
    detected: string[]
    leak: { leaked_families: string[]; shift_b: number; share_knowers: number }
    control_false_flags: string[]
    control_tested: number
  }
  exposure?: {
    spec: string
    r_max: number
    baseline_verify: Record<string, number>
    sh_verify: Record<string, number>
    per_family: FamExp[]
  }
}

const n2 = (x: number | null | undefined, d = 2) => (x === null || x === undefined ? '—' : formatDecimal(x, d))

function ClassChip({ cls }: { cls: string }) {
  if (cls === 'C') return <Chip tone="danger">C</Chip>
  if (cls === 'B') return <Chip tone="brand">B</Chip>
  return <Chip>{cls === 'n/a' ? 'мало данных' : cls}</Chip>
}

/** «Качество заданий»: DIF (синтетика, проверка метода), дрейф сложности и экспозиция семейств. */
export default function Quality() {
  const q = useLabQuality()
  const [view, setView] = useState<View>('dif')
  const syn = (q.data?.synthetic ?? null) as Synthetic | null
  return (
    <div className="flex flex-col gap-8">
      <LabHead
        caption="Лаборатория качества теста"
        title="Качество заданий"
        lead="Справедливость и стабильность банка: не тяжелее ли задание для какой-то группы при одинаковом уровне (DIF по Мантелю–Хензелю, классы ETS), не стало ли оно легче после слива (дрейф сложности) и не слишком ли часто его показывают (экспозиция)."
      />
      <Segmented aria-label="Раздел" options={VIEWS} value={view} onChange={setView} />
      {q.isPending ? (
        <LabSkeleton rows={5} />
      ) : q.isError ? (
        <LabError error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <>
          <Alert tone="info">{q.data.synthetic_note}</Alert>
          {view === 'dif' && <Dif syn={syn} />}
          {view === 'drift' && <Drift data={q.data} syn={syn} />}
          {view === 'exposure' && <Exposure data={q.data} syn={syn} />}
        </>
      )}
    </div>
  )
}

function NoRun() {
  return (
    <Panel variant="panel" radius="lg">
      <EmptyState
        title="Прогона на синтетике ещё нет"
        description={
          <>
            Выполните <code className="font-mono">uv run python -m eval.fairness</code> в каталоге backend.
          </>
        }
      />
    </Panel>
  )
}

function Dif({ syn }: { syn: Synthetic | null }) {
  const d = syn?.dif
  if (!d) return <NoRun />
  const flagged = d.rows.filter((r) => r.planted_shift === null && (r.dif_class === 'B' || r.dif_class === 'C'))
  return (
    <div className="flex flex-col gap-8">
      <Counters
        items={[
          { value: formatInt(d.n_sessions), label: 'сессий в анализе' },
          { value: formatInt(d.real_total.tested), label: 'пар «семейство × группа»' },
          { value: d.real_share_A === null ? '—' : `${Math.round(d.real_share_A * 100)}%`, label: 'класс A у настоящих заданий', tone: 'brand' },
          { value: formatInt(d.real_total.C), label: 'класс C у настоящих' },
        ]}
      />
      <section className="flex flex-col gap-3">
        <h2 className="t-name text-ink">Внедрённые смещённые задания</h2>
        <p className="t-small text-ink-3">
          Фокальной группе сложность повышена на заданную величину; отчёт обязан их найти. Δ &lt; 0 — тяжелее для фокальной группы.
        </p>
        <Table head={['Группа', 'Семейство', 'Сдвиг, логиты', 'MH D-DIF', 'Класс']}>
          {d.planted.map((p) => (
            <tr key={`${p.group}-${p.family_id}`} className="border-b border-hairline last:border-0">
              <Td>{GROUP_RU[p.group] ?? p.group}</Td>
              <Td mono>{p.family_id}</Td>
              <Td mono>{n2(p.shift)}</Td>
              <Td mono>{n2(p.delta)}</Td>
              <Td>
                <ClassChip cls={p.dif_class ?? 'n/a'} />
              </Td>
            </tr>
          ))}
        </Table>
      </section>
      <section className="flex flex-col gap-3">
        <h2 className="t-name text-ink">Настоящие семейства банка</h2>
        <Table head={['Фокальная группа', 'Проверено пар', 'A', 'B', 'C']}>
          {Object.entries(d.real_by_group).map(([g, c]) => (
            <tr key={g} className="border-b border-hairline last:border-0">
              <Td>{GROUP_RU[g] ?? g}</Td>
              <Td mono>{c.tested}</Td>
              <Td mono>{c.A}</Td>
              <Td mono>{c.B}</Td>
              <Td mono>{c.C}</Td>
            </tr>
          ))}
        </Table>
        {flagged.length > 0 && (
          <>
            <h3 className="t-label text-ink-2">Пары с классом B или C</h3>
            <Table head={['Группа', 'Семейство', 'Верно: опорная / фокальная', 'MH D-DIF', 'Класс']}>
              {flagged.slice(0, 20).map((r) => (
                <tr key={`${r.group}-${r.family_id}`} className="border-b border-hairline last:border-0">
                  <Td>{GROUP_RU[r.group] ?? r.group}</Td>
                  <Td mono>{r.family_id}</Td>
                  <Td mono>
                    {n2(r.p_ref)} / {n2(r.p_focal)}
                  </Td>
                  <Td mono>{n2(r.delta)}</Td>
                  <Td>
                    <ClassChip cls={r.dif_class} />
                  </Td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </section>
    </div>
  )
}

function Drift({ data, syn }: { data: QualityData; syn: Synthetic | null }) {
  const rows = [...data.drift].sort((a, b) => (a.d_b ?? 0) - (b.d_b ?? 0))
  const s = syn?.drift
  return (
    <div className="flex flex-col gap-8">
      <Counters
        items={[
          { value: formatInt(data.drift_flagged), label: 'семейств с флагом «дрейф»', tone: data.drift_flagged ? 'brand' : undefined },
          { value: `≤ −${n2(data.drift_threshold_b)}`, label: 'порог Δb, логиты' },
          { value: formatInt(data.drift_min_n), label: 'минимум ответов' },
        ]}
      />
      <section className="flex flex-col gap-3">
        <h2 className="t-name text-ink">Живые данные платформы</h2>
        {rows.length === 0 ? (
          <EmptyState
            title="Ответов пока нет"
            description="Сдвиг считается по ответам кандидатов с согласием на калибровку после последней калибровки семейства."
          />
        ) : (
          <Table head={['Семейство', 'Ответов', 'Верно факт / ожид.', 'Δb', '95% ДИ', 'Флаг']}>
            {rows.map((r) => (
              <tr key={r.family_id} className="border-b border-hairline last:border-0">
                <Td mono>{r.family_id}</Td>
                <Td mono>{r.n}</Td>
                <Td mono>
                  {n2(r.observed_p)} / {n2(r.expected_p)}
                </Td>
                <Td mono>{n2(r.d_b)}</Td>
                <Td mono>{r.ci_lo === null || r.ci_hi === null ? '—' : `${n2(r.ci_lo)} … ${n2(r.ci_hi)}`}</Td>
                <Td>
                  {r.flag === 'drift' ? (
                    <Chip tone="danger">возможная утечка</Chip>
                  ) : r.flag === 'watch' ? (
                    <Chip tone="brand">наблюдать</Chip>
                  ) : (
                    <Chip>{r.flag === 'ok' ? 'в норме' : 'мало данных'}</Chip>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </section>
      {s && (
        <section className="flex flex-col gap-2">
          <h2 className="t-name text-ink">Проверка на синтетике</h2>
          <p className="t-body text-ink-2">
            {s.leak.leaked_families.length} семейств «утекли» ({Math.round(s.leak.share_knowers * 100)}% знают ответы, сложность ниже на{' '}
            {n2(-s.leak.shift_b)}): монитор нашёл {s.detected.length}; в контроле без утечки ложных флагов {s.control_false_flags.length} из{' '}
            {s.control_tested}.
          </p>
        </section>
      )}
    </div>
  )
}

function Bars({ rows, rMax }: { rows: { id: string; a: number; b?: number }[]; rMax: number }) {
  return (
    <div className="flex flex-col gap-1.5" role="list" aria-label="Экспозиция семейств">
      {rows.map((r) => (
        <div key={r.id} role="listitem" className="grid grid-cols-[minmax(0,13rem)_1fr_3.5rem] items-center gap-3">
          <span className="truncate font-mono text-[12px] text-ink-2" title={r.id}>
            {r.id}
          </span>
          <div className="relative h-4 rounded-sm bg-panel">
            <div
              className={`absolute inset-y-0 left-0 rounded-sm ${r.a > rMax ? 'bg-danger-text' : 'bg-brand'}`}
              style={{ width: `${Math.min(100, r.a * 100)}%` }}
            />
            {r.b !== undefined && (
              <div className="absolute inset-y-1 left-0 rounded-sm bg-ink-3 opacity-60" style={{ width: `${Math.min(100, r.b * 100)}%` }} />
            )}
            <div className="absolute inset-y-[-2px] w-px bg-ink" style={{ left: `${rMax * 100}%` }} aria-hidden />
          </div>
          <span className="text-right font-mono text-[12px] tabular-nums text-ink-2">{Math.round(r.a * 100)}%</span>
        </div>
      ))}
    </div>
  )
}

function Exposure({ data, syn }: { data: QualityData; syn: Synthetic | null }) {
  const e = syn?.exposure
  const live = data.exposure.slice(0, 25)
  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <h2 className="t-name text-ink">Живые данные платформы</h2>
        {live.length === 0 ? (
          <EmptyState title="Решённых сессий пока нет" />
        ) : (
          <>
            <p className="t-small text-ink-3">
              Доля решённых сессий, где показано семейство; вертикальная черта — целевой максимум {Math.round(data.exposure_r_max * 100)}%.
            </p>
            <Bars rows={live.map((r) => ({ id: r.family_id, a: r.exposure }))} rMax={data.exposure_r_max} />
          </>
        )}
      </section>
      {e && (
        <section className="flex flex-col gap-4">
          <h2 className="t-name text-ink">Симпсон–Хеттер на синтетике ({e.spec})</h2>
          <Counters
            items={[
              {
                value: `${Math.round(e.baseline_verify.max_exposure * 100)}% → ${Math.round(e.sh_verify.max_exposure * 100)}%`,
                label: 'максимум экспозиции',
                tone: 'brand',
              },
              { value: `${e.baseline_verify.families_over_rmax} → ${e.sh_verify.families_over_rmax}`, label: `семейств выше ${Math.round(e.r_max * 100)}%` },
              { value: `${n2(e.baseline_verify.mean_se, 3)} → ${n2(e.sh_verify.mean_se, 3)}`, label: 'средняя погрешность' },
            ]}
          />
          <p className="t-small text-ink-3">Цветная полоса — без контроля, серая тонкая — с контролем. Контроль выключен по умолчанию.</p>
          <Bars
            rows={[...e.per_family]
              .sort((x, y) => y.baseline - x.baseline)
              .slice(0, 20)
              .map((f) => ({ id: f.family_id, a: f.baseline, b: f.sh }))}
            rMax={e.r_max}
          />
        </section>
      )}
    </div>
  )
}
