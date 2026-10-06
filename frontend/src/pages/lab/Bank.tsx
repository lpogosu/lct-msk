import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage } from '@/api/errors'
import { usePrecompute, useTwinCoverage, type TwinCoverageFamily } from '@/features/anticheat/api'
import { GRADE_RU, useLabBank, type BankFamily } from '@/features/anticheat/labApi'
import { cn } from '@/lib/cn'
import { formatDecimal, formatInt } from '@/lib/format'
import { Alert, Button, Counters, Panel, useToast } from '@/ui'
import { Select } from '@/ui/Select'
import { Chip, LabError, LabHead, LabSkeleton, Table, Td } from './parts'

const SPECS = [
  { value: '', label: 'Все направления' },
  { value: 'backend_python', label: 'Backend (Python)' },
  { value: 'frontend_js', label: 'Frontend (JS/TS)' },
  { value: 'qa', label: 'QA' },
  { value: 'devops', label: 'DevOps' },
]

const FORMAT_RU: Record<string, string> = {
  predict_output: 'вывод программы',
  sql_result: 'результат запроса',
  mcq: 'выбор варианта',
  bug_line: 'строка с ошибкой',
  parsons: 'порядок строк',
}

function mechanismText(c: TwinCoverageFamily | undefined): { label: string; tone: 'neutral' | 'brand' } {
  if (!c) return { label: '…', tone: 'neutral' }
  if (c.mechanism === 'own') return { label: 'свой двойник', tone: 'brand' }
  if (c.mechanism === 'generic') return { label: 'общий мутатор', tone: 'brand' }
  return { label: 'нет по форме', tone: 'neutral' }
}

/** «Банк заданий»: семейства с параметрами, пулом, «3 клона рядом» и покрытием двойниками. */
export default function Bank() {
  const [spec, setSpec] = useState('')
  const bank = useLabBank(spec)
  const cov = useTwinCoverage()
  const covBy = useMemo(() => new Map(cov.data?.families.map((f) => [f.family_id, f]) ?? []), [cov.data])

  return (
    <div className="flex flex-col gap-8">
      <LabHead
        caption="Лаборатория качества теста"
        title="Банк заданий"
        lead="Семейства заданий, их сложность и пул одноразовых клонов. Для каждого видно, будет ли у экземпляра двойник в буфере и сколько их уже посчитано."
        actions={
          <Select aria-label="Направление" value={spec} onChange={(e) => setSpec(e.target.value)} wrapperClassName="min-w-[240px]">
            {SPECS.map((s) => (
              <option key={s.value || 'all'} value={s.value}>
                {s.label}
              </option>
            ))}
          </Select>
        }
      />

      <Coverage />

      {bank.isPending ? (
        <LabSkeleton rows={6} />
      ) : bank.isError ? (
        <LabError error={bank.error} onRetry={() => void bank.refetch()} />
      ) : (
        <>
          <p className="t-body text-ink-2">
            Семейств: {formatInt(bank.data.total)}
            {Object.entries(bank.data.by_status).map(([k, v]) => ` · ${k}: ${v}`)}
          </p>
          <Table head={['Семейство', 'Формат', 'Уровень', 'Сложность', 'Пул', 'Двойник в буфере', '']}>
            {bank.data.families.map((f) => (
              <Row key={f.family_id} f={f} twin={covBy.get(f.family_id)} />
            ))}
          </Table>
        </>
      )}
    </div>
  )
}

function Row({ f, twin }: { f: BankFamily; twin: TwinCoverageFamily | undefined }) {
  const p = f.params[0]
  const m = mechanismText(twin)
  return (
    <tr className="border-b border-hairline align-top transition-colors hover:bg-panel/60">
      <Td className="max-w-[320px]">
        <span className="block t-label text-ink">{f.title}</span>
        <span className="block font-mono text-[12px] text-ink-3">{f.family_id}</span>
        <span className="block t-small text-ink-3">{f.module.label}</span>
      </Td>
      <Td>{FORMAT_RU[f.format] ?? f.format}</Td>
      <Td>
        {GRADE_RU[f.target_grade] ?? f.target_grade}
        {f.is_anchor && <span className="ml-1.5 t-small text-brand">якорь</span>}
      </Td>
      <Td mono>
        {p ? `${formatDecimal(p.difficulty, 2)} ± ${formatDecimal(p.difficulty_sd, 2)}` : '—'}
        {p && <span className="block font-sans text-[12px] text-ink-3">{p.source === 'prior' ? 'оценка экспертов' : 'по ответам'}</span>}
      </Td>
      <Td mono>
        {f.pool.free}/{f.pool.total}
        <span className="block font-sans text-[12px] text-ink-3">выдано {f.pool.issued}</span>
      </Td>
      <Td>
        <Chip tone={m.tone}>{m.label}</Chip>
        {twin && twin.mechanism !== 'none' && (
          <span className="mt-1 block t-small text-ink-3">
            готово {twin.twins_ok}
            {twin.pending > 0 ? ` · ждут расчёта ${twin.pending}` : ''}
            {twin.twins_none > 0 ? ` · не вышло ${twin.twins_none}` : ''}
            {twin.shown > 0 ? ` · выдано ${twin.shown}` : ''}
          </span>
        )}
        {twin?.mechanism === 'none' && twin.reason_ru && <span className="mt-1 block max-w-[240px] t-small text-ink-3">{twin.reason_ru}</span>}
        {twin && (twin.top_reasons?.length ?? 0) > 0 && twin.twins_ok === 0 && (
          <span className="mt-1 block max-w-[240px] t-small text-ink-3">{String(twin.top_reasons?.[0]?.reason_ru ?? '')}</span>
        )}
      </Td>
      <Td>
        <Link to={`/lab/bank/${encodeURIComponent(f.family_id)}`} className="t-label whitespace-nowrap text-brand no-underline hover:underline">
          3 клона рядом
        </Link>
      </Td>
    </tr>
  )
}

/** Сводка покрытия двойниками и кнопка «Посчитать двойники»: расчёт — запуски кода, делается заранее, а не при выдаче. */
function Coverage() {
  const toast = useToast()
  const cov = useTwinCoverage()
  const run = usePrecompute()
  const t = cov.data?.totals
  async function start() {
    try {
      const r = await run.mutateAsync({})
      if (r.accepted) toast.success(r.message)
      else toast.show({ message: r.message })
    } catch (e) {
      toast.error(errorMessage(e), 'Не удалось запустить расчёт')
    }
  }
  return (
    <Panel variant="tint" radius="lg" className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex max-w-[760px] flex-col gap-1.5">
          <h2 className="t-name text-ink">Покрытие двойниками</h2>
          <p className="t-body text-ink-2">
            {cov.data?.note_ru ?? 'Двойник считается заранее: после генерации пула запустите расчёт.'}
          </p>
        </div>
        <Button onClick={() => void start()} loading={run.isPending || t?.running === 1} variant="outline">
          {t?.running === 1 ? 'Считаем…' : 'Посчитать двойники'}
        </Button>
      </div>
      {cov.isError && <Alert tone="danger">{errorMessage(cov.error)}</Alert>}
      {t && (
        <Counters
          items={[
            { value: `${t.families_with_twin}/${t.families}`, label: 'семейств, где двойник возможен', tone: 'brand' },
            { value: formatInt(t.families_own_twin), label: 'со своим двойником (QA, DevOps)' },
            { value: formatInt(t.pool_free), label: 'свободных экземпляров' },
            { value: formatInt(t.twins_ok), label: 'двойников готово', tone: 'brand' },
            { value: formatInt(t.pending), label: 'ждут расчёта' },
            { value: formatInt(t.shown), label: 'выдано кандидатам' },
          ]}
          className={cn(cov.isFetching && 'opacity-70')}
        />
      )}
    </Panel>
  )
}
