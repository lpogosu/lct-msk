import { useState, type ReactNode } from 'react'
import { errorMessage } from '@/api/errors'
import { downloadFspCsv, useFspDashboard, type BreakRow, type DashCell, type Dashboard, type DemandRow } from '@/features/fsp/api'
import { cn } from '@/lib/cn'
import { formatDecimal, formatInt } from '@/lib/format'
import { LabError, LabHead, LabSkeleton, Table, Td } from '@/pages/lab/parts'
import { Alert, Button, Counters, Panel, Segmented } from '@/ui'
import { Tabs } from '@/ui/Tabs'

// «Кадровый резерв ФСП»: воронка «спортсмен → нанят», разрезы, динамика, спрос против предложения.
// Бэкенд отдаёт только агрегаты; ячейки с числом людей меньше k приходят как «<5» (n = null) — рисуем их штриховкой.

const HATCH = {
  backgroundImage: 'repeating-linear-gradient(135deg, #C8C9CA 0 2px, transparent 2px 6px)',
} as const

const STAGE_SHORT: Record<string, string> = {
  linked: 'Привязан ФСП ID',
  tested: 'Тест',
  confirmed: 'Категория',
  shortlisted: 'В подборках',
  invited: 'Приглашён',
  mutual: '«Взаимно»',
  hired: 'Нанят',
}
const TABLE_STAGES = ['linked', 'tested', 'confirmed', 'invited', 'mutual', 'hired'] as const
const REGION_STAGES = ['linked', 'confirmed', 'invited', 'mutual', 'hired'] as const

const STATUS: Record<string, { label: string; tone: 'danger' | 'brand' | 'neutral' }> = {
  deficit: { label: 'Не хватает кадров', tone: 'danger' },
  balance: { label: 'Баланс', tone: 'brand' },
  surplus: { label: 'Избыток', tone: 'neutral' },
  few_data: { label: 'Мало данных', tone: 'neutral' },
  no_demand: { label: 'Нет спроса', tone: 'neutral' },
}

function CellText({ cell, mono = true }: { cell: DashCell | undefined; mono?: boolean }) {
  if (!cell) return <span className="text-ink-3">—</span>
  if (cell.suppressed)
    return (
      <span className={cn('text-ink-3', mono && 'font-mono')} title="Скрыто: в ячейке меньше 5 человек (k-анонимность)">
        {cell.text}
      </span>
    )
  return <span className={cn(mono && 'font-mono tabular-nums')}>{cell.n === null ? cell.text : formatInt(cell.n)}</span>
}

function Section({
  id,
  title,
  lead,
  children,
}: {
  id: string
  title: string
  lead?: ReactNode
  children: ReactNode
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4">
      <div className="flex max-w-[820px] flex-col gap-1.5">
        <h2 id={id} className="t-title text-ink">
          {title}
        </h2>
        {lead && <p className="t-body text-ink-2">{lead}</p>}
      </div>
      {children}
    </section>
  )
}

function Rate({ value }: { value: number | null }) {
  return <span className="font-mono text-[13px] tabular-nums text-ink-3">{value === null ? '' : `${formatDecimal(value)} %`}</span>
}

// ---------------------------------------------------------------------------------------------- воронка

function Funnel({ data }: { data: Dashboard }) {
  const first = data.funnel[0]
  const baseF = first.fsp.n ?? 0
  const baseO = first.other.n ?? 0
  const width = (cell: DashCell, base: number) => (cell.n === null || base === 0 ? 0 : Math.max(1.5, (cell.n / base) * 100))
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 pb-2 t-small text-ink-3">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm bg-brand" aria-hidden="true" /> Спортсмены ФСП
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm bg-bar" aria-hidden="true" /> Остальные кандидаты (для сравнения)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm" style={HATCH} aria-hidden="true" /> Скрыто: меньше {data.k_min}
        </span>
      </div>
      <ol className="m-0 flex list-none flex-col p-0">
        {data.funnel.map((s, i) => (
          <li
            key={s.key}
            className="grid items-center gap-x-4 gap-y-1.5 border-t border-hairline py-3 first:border-t-0 sm:grid-cols-[minmax(160px,260px)_minmax(0,1fr)_64px]"
          >
            <span className="t-label text-ink">{s.label}</span>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Bar cell={s.fsp} pct={width(s.fsp, baseF)} tone="brand" label="ФСП" />
              <Bar cell={s.other} pct={width(s.other, baseO)} tone="bar" label={i === 0 ? 'остальные (все активные кандидаты)' : 'остальные'} />
            </div>
            <div className="flex items-center justify-between gap-3 sm:flex-col sm:items-end sm:gap-1.5">
              <Rate value={s.fsp_rate} />
              <Rate value={s.other_rate} />
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

function Bar({ cell, pct, tone, label }: { cell: DashCell; pct: number; tone: 'brand' | 'bar'; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="h-3.5 min-w-0 flex-1" role="img" aria-label={`${label}: ${cell.text}`}>
        {cell.suppressed ? (
          <span className="block h-full w-16 rounded-sm" style={HATCH} />
        ) : (
          <span className={cn('block h-full rounded-sm', tone === 'brand' ? 'bg-brand' : 'bg-bar')} style={{ width: `${pct}%` }} />
        )}
      </div>
      <span className="w-14 shrink-0 text-right font-mono text-[13px] tabular-nums text-ink">
        <CellText cell={cell} />
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- динамика

const SERIES = [
  { key: 'linked', label: 'Привязали ФСП ID' },
  { key: 'invited', label: 'Приглашения спортсменам' },
  { key: 'mutual', label: '«Взаимно»' },
  { key: 'hired', label: 'Наняты' },
] as const

type SeriesKey = (typeof SERIES)[number]['key']

function Timeline({ data }: { data: Dashboard }) {
  const [key, setKey] = useState<SeriesKey>('invited')
  const cells = data.timeline.series[key] ?? []
  const max = Math.max(1, ...cells.map((c) => c.n ?? 0))
  const fmt = (iso: string) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(new Date(iso))
  return (
    <div className="flex flex-col gap-4">
      <Tabs aria-label="Показатель" options={SERIES.map((s) => ({ value: s.key, label: s.label }))} value={key} onChange={setKey} />
      <figure className="m-0">
        <ol className="m-0 flex h-36 list-none items-end gap-1.5 p-0 sm:gap-2.5">
          {cells.map((c, i) => (
            <li
              key={i}
              className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1"
              title={`Неделя с ${fmt(data.timeline.weeks[i])}: ${c.text}`}
            >
              <span className="font-mono text-[11px] tabular-nums text-ink-3">{c.suppressed ? '<5' : c.n}</span>
              {c.suppressed ? (
                <span className="block h-4 w-full rounded-t-[4px]" style={HATCH} />
              ) : (
                <span className="block w-full rounded-t-[4px] bg-brand" style={{ height: `${Math.max(2, ((c.n ?? 0) / max) * 100)}%` }} />
              )}
            </li>
          ))}
        </ol>
        <ol className="m-0 mt-2 flex list-none gap-1.5 p-0 t-small text-ink-3 sm:gap-2.5" aria-hidden="true">
          {data.timeline.weeks.map((w, i) => (
            <li key={w} className="min-w-0 flex-1 truncate text-center">
              {i % 2 === 0 ? fmt(w) : ''}
            </li>
          ))}
        </ol>
        <figcaption className="mt-3 t-small text-ink-3">
          По неделям (с понедельника). Штриховка — в неделе меньше {data.k_min} человек, число скрыто.
        </figcaption>
      </figure>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- разрезы

type BreakTab = 'discipline' | 'district' | 'region' | 'spec'

function BreakTable({ rows, stages, first }: { rows: BreakRow[]; stages: readonly string[]; first: string }) {
  return (
    <Table head={[first, ...stages.map((s) => STAGE_SHORT[s])]}>
      {rows.map((r) => (
        <tr key={r.key} className="border-b border-hairline last:border-0">
          <Td className="text-ink">{r.label}</Td>
          {stages.map((s) => (
            <Td key={s}>
              <CellText cell={r.stages[s]} />
            </Td>
          ))}
        </tr>
      ))}
    </Table>
  )
}

function Breakdowns({ data }: { data: Dashboard }) {
  const [tab, setTab] = useState<BreakTab>('discipline')
  const regionRows = data.by_region.other ? [...data.by_region.rows, data.by_region.other] : data.by_region.rows
  return (
    <div className="flex flex-col gap-4">
      <Tabs
        aria-label="Разрез"
        options={[
          { value: 'discipline', label: 'Дисциплины ФСП' },
          { value: 'district', label: 'Федеральные округа' },
          { value: 'region', label: 'Регионы' },
          { value: 'spec', label: 'Специализация × грейд' },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'discipline' && (
        <>
          <BreakTable rows={data.by_discipline} stages={TABLE_STAGES} first="Дисциплина" />
          <p className="t-small text-ink-3">
            Спортсмен с результатами в двух дисциплинах учтён в обеих строках, поэтому строки в сумме больше общего числа.
          </p>
        </>
      )}
      {tab === 'district' && (
        <>
          <BreakTable rows={data.by_district} stages={TABLE_STAGES} first="Округ" />
          <p className="t-small text-ink-3">
            Регион — из профиля кандидата. Если в стадии скрыта одна строка, прячем и соседнюю наименьшую: иначе скрытое число можно вычесть из итога.
          </p>
        </>
      )}
      {tab === 'region' && (
        <>
          <BreakTable rows={regionRows} stages={REGION_STAGES} first="Регион" />
          <p className="t-small text-ink-3">
            Отдельной строкой — регионы, где не меньше {data.k_min} спортсменов; остальные слиты в «прочие» (регионов с участниками:{' '}
            {data.by_region.regions_total}).
          </p>
        </>
      )}
      {tab === 'spec' && (
        <>
          <Table head={['Специализация', ...data.spec_grade.grades.map((g) => g.label)]}>
            {data.spec_grade.rows.map((r) => (
              <tr key={r.key} className="border-b border-hairline last:border-0">
                <Td className="text-ink">{r.label}</Td>
                {data.spec_grade.grades.map((g) => (
                  <Td key={g.key}>
                    <CellText cell={r.cells[g.key]} />
                  </Td>
                ))}
              </tr>
            ))}
          </Table>
          <p className="t-small text-ink-3">Спортсмены ФСП с подтверждённой категорией (по результату теста, а не по резюме).</p>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- спрос и зарплаты

function StatusChip({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.few_data
  return (
    <span
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-pill px-2.5 py-0.5 t-small',
        s.tone === 'danger' && 'bg-[#FFF1F3] text-danger-text',
        s.tone === 'brand' && 'bg-tint text-brand',
        s.tone === 'neutral' && 'bg-panel text-ink-2',
      )}
    >
      {s.label}
    </span>
  )
}

function MiniBar({ cell, max, tone }: { cell: DashCell; max: number; tone: 'brand' | 'bar' }) {
  return (
    <div className="flex items-center gap-3">
      <span className="block h-2.5 w-24 shrink-0 overflow-hidden rounded-sm bg-panel" aria-hidden="true">
        {cell.suppressed ? (
          <span className="block h-full w-8" style={HATCH} />
        ) : (
          <span className={cn('block h-full', tone === 'brand' ? 'bg-brand' : 'bg-bar')} style={{ width: `${((cell.n ?? 0) / max) * 100}%` }} />
        )}
      </span>
      <CellText cell={cell} />
    </div>
  )
}

function Demand({ rows, k }: { rows: DemandRow[]; k: number }) {
  const [onlyDeficit, setOnlyDeficit] = useState(false)
  const shown = onlyDeficit ? rows.filter((r) => r.status === 'deficit') : rows
  const max = Math.max(1, ...rows.map((r) => Math.max(r.demand_companies.n ?? 0, r.supply_fsp.n ?? 0)))
  return (
    <div className="flex flex-col gap-4">
      <Segmented
        aria-label="Какие категории показывать"
        options={[
          { value: 'all', label: 'Все категории' },
          { value: 'deficit', label: 'Только «не хватает»' },
        ]}
        value={onlyDeficit ? 'deficit' : 'all'}
        onChange={(v) => setOnlyDeficit(v === 'deficit')}
      />
      <Table head={['Категория', 'Компаний со спросом', 'Спортсменов ФСП', 'Всего на платформе', 'Оценка']}>
        {shown.map((r) => (
          <tr key={`${r.spec}-${r.grade}`} className="border-b border-hairline last:border-0">
            <Td className="text-ink">
              {r.spec_label} · {r.grade_label}
            </Td>
            <Td>
              <MiniBar cell={r.demand_companies} max={max} tone="bar" />
            </Td>
            <Td>
              <MiniBar cell={r.supply_fsp} max={max} tone="brand" />
            </Td>
            <Td>
              <CellText cell={r.supply_all} />
            </Td>
            <Td>
              <StatusChip status={r.status} />
            </Td>
          </tr>
        ))}
      </Table>
      {shown.length === 0 && <p className="t-body text-ink-2">Категорий с нехваткой нет.</p>}
      <p className="t-small text-ink-3">
        Спрос — компании, у которых за период была потребность или есть вакансия в категории; предложение — спортсмены ФСП с подтверждённой
        категорией, открытые к предложениям. «Не хватает» — меньше 0,5 спортсмена на компанию со спросом, «избыток» — больше одного. Спрос скрыт, пока в категории
        меньше 2 компаний, предложение — меньше {k} человек.
      </p>
    </div>
  )
}

function Salary({ data }: { data: Dashboard }) {
  const [basis, setBasis] = useState<'net' | 'gross'>('net')
  const word = basis === 'net' ? 'на руки' : 'до НДФЛ'
  const val = (c: { median_net: number | null; median_gross: number | null }) => (basis === 'net' ? c.median_net : c.median_gross)
  const money = (n: number | null) =>
    n === null ? <span className="text-ink-3">—</span> : <span className="font-mono tabular-nums">{formatInt(n / 1000)} тыс. ₽</span>
  return (
    <div className="flex flex-col gap-4">
      <Segmented
        aria-label="База вилки"
        options={[
          { value: 'net', label: 'На руки' },
          { value: 'gross', label: 'До НДФЛ' },
        ]}
        value={basis}
        onChange={setBasis}
      />
      <Table head={['Грейд', 'ФСП: кандидатов', `ФСП: медиана, ${word}`, 'Остальные: кандидатов', `Остальные: медиана, ${word}`]}>
        {data.salary.rows.map((r) => (
          <tr key={r.key} className="border-b border-hairline last:border-0">
            <Td className="text-ink">{r.label}</Td>
            <Td>
              <CellText cell={r.fsp.n} />
            </Td>
            <Td>{money(val(r.fsp))}</Td>
            <Td>
              <CellText cell={r.other.n} />
            </Td>
            <Td>{money(val(r.other))}</Td>
          </tr>
        ))}
      </Table>
      <p className="t-small text-ink-3">
        {data.salary.basis} Разница между группами на демо-данных — шум: эффекта «ФСП сильнее» мы в них не закладывали.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- страница

export default function Reserve() {
  const q = useFspDashboard()
  const [csvBusy, setCsvBusy] = useState(false)
  const [csvError, setCsvError] = useState<string | null>(null)

  async function exportCsv() {
    setCsvError(null)
    setCsvBusy(true)
    try {
      await downloadFspCsv()
    } catch (e) {
      setCsvError(errorMessage(e))
    } finally {
      setCsvBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-10">
      <LabHead
        caption="Федерация спортивного программирования"
        title="Кадровый резерв"
        lead="Как спортсмены ФСП проходят путь от соревнований до найма: сколько прошли тест и подтвердили категорию, попали в подборки, получили приглашение, ответили «Взаимно» и были наняты. И где работодателям не хватает кадров."
        actions={
          <Button variant="outline" loading={csvBusy} onClick={() => void exportCsv()} disabled={!q.data}>
            Скачать CSV
          </Button>
        }
      />
      {csvError && <Alert tone="danger">{csvError}</Alert>}
      {q.isPending ? (
        <LabSkeleton rows={6} />
      ) : q.isError ? (
        <LabError error={q.error} onRetry={() => void q.refetch()} title="Не удалось собрать панель" />
      ) : (
        <Body data={q.data} />
      )}
    </div>
  )
}

function Body({ data }: { data: Dashboard }) {
  const f = Object.fromEntries(data.funnel.map((s) => [s.key, s]))
  const testedRate = f.tested?.fsp_rate
  return (
    <>
      {data.synthetic && (
        <Alert title="Демо-данные синтетические">
          {data.note} Ячейки, где меньше {data.k_min} человек, скрыты («&lt;{data.k_min}»).
        </Alert>
      )}

      <Counters
        items={[
          { value: <CellText cell={f.linked.fsp} />, label: 'спортсменов ФСП на платформе', tone: 'brand' },
          { value: testedRate == null ? '—' : `${formatDecimal(testedRate)} %`, label: 'прошли тест' },
          { value: <CellText cell={f.confirmed.fsp} />, label: 'с подтверждённой категорией' },
          { value: <CellText cell={f.mutual.fsp} />, label: '«Взаимно»' },
          { value: <CellText cell={f.hired.fsp} />, label: 'нанято' },
          { value: <CellText cell={data.companies.hired} />, label: 'компаний наняли спортсменов ФСП' },
        ]}
      />

      <Section
        id="funnel"
        title="Воронка: от соревнований до найма"
        lead="Стадии вложены: каждая следующая — часть предыдущей. Рядом — все остальные кандидаты платформы, чтобы видеть, не отстают ли спортсмены. Справа — доля от предыдущей стадии (сверху ФСП, снизу остальные)."
      >
        <Panel variant="outline" radius="xl" padding="md">
          <Funnel data={data} />
        </Panel>
      </Section>

      <Section id="timeline" title="Динамика по неделям" lead="Как растёт число привязок, приглашений, «Взаимно» и найма за последние недели.">
        <Panel variant="outline" radius="xl" padding="md">
          <Timeline data={data} />
        </Panel>
      </Section>

      <Section
        id="breakdowns"
        title="Разрезы"
        lead="Те же стадии по дисциплинам ФСП (продуктовое, алгоритмическое, ИБ, робототехника, БАС), округам, регионам и категориям."
      >
        <Panel variant="outline" radius="xl" padding="md">
          <Breakdowns data={data} />
        </Panel>
      </Section>

      <Section id="demand" title="Где не хватает кадров" lead="Спрос работодателей по категориям «специализация × грейд» против предложения спортсменов ФСП.">
        <Panel variant="outline" radius="xl" padding="md">
          <Demand rows={data.demand_supply} k={data.k_min} />
        </Panel>
      </Section>

      <Section id="salary" title="Предложенная зарплата: ФСП и остальные" lead="Медиана середины вилки из первой оферты в приглашении, по грейдам.">
        <Panel variant="outline" radius="xl" padding="md">
          <Salary data={data} />
        </Panel>
      </Section>

      <Section id="companies" title="Компании, нанимающие спортсменов ФСП" lead="Только число компаний: названия в панель не попадают.">
        <Counters
          items={[
            { value: <CellText cell={data.companies.invited} />, label: 'пригласили спортсменов' },
            { value: <CellText cell={data.companies.mutual} />, label: 'получили «Взаимно»' },
            { value: <CellText cell={data.companies.hired} />, label: 'наняли', tone: 'brand' },
          ]}
        />
      </Section>

      <Panel variant="panel" radius="xl" padding="md" className="flex flex-col gap-2">
        <h2 className="t-name text-ink">Что видит ФСП, а что нет</h2>
        <p className="t-body text-ink-2">{data.privacy_note}</p>
        <p className="t-small text-ink-3">
          Отсутствие истории ФСП у кандидата не штраф: на подбор и категорию достижения ФСП не влияют, а сравнение «ФСП / остальные» здесь
          описательное. Обновлено: {new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long', timeStyle: 'short' }).format(new Date(data.as_of))}.
        </p>
      </Panel>
    </>
  )
}
