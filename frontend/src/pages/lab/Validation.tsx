import { useState, type ReactNode } from 'react'
import { isApiException } from '@/api/errors'
import { useValidationReport, type ValidationReport } from '@/features/anticheat/api'
import { formatDecimal, formatInt } from '@/lib/format'
import { Counters, EmptyState, Panel } from '@/ui'
import { Tabs } from '@/ui/Tabs'
import { LabError, LabHead, LabSkeleton, Table, Td } from './parts'

type Json = unknown
type Obj = Record<string, Json>

const SECTION_RU: Record<string, string> = {
  testing: 'Тестирование',
  categorization: 'Категоризация',
  matching: 'Подбор',
  pairs: 'Набор пар',
  population: 'Популяция',
  tiles: 'Плитки',
}

// Подписи частых ключей: остальные показываем как есть (это имена метрик из отчёта, они стабильны)
const KEY_RU: Record<string, string> = {
  n: 'наблюдений',
  seed: 'seed',
  accuracy: 'точность',
  decision_accuracy: 'точность решения',
  decision_agreement: 'согласие решений',
  decision_kappa: 'каппа решений',
  grade_exact: 'точное совпадение грейда',
  grade_within1: 'грейд в пределах ступени',
  grade_qwk: 'взвешенная каппа грейда',
  theta_r: 'корреляция уровней',
  reliability: 'надёжность',
  false_upgrade_rate: 'доля ложных повышений',
  instances: 'экземпляров',
  shared_between_candidates: 'общих у разных кандидатов',
  macro_f1: 'macro-F1',
  baseline_choice_macro_f1: 'macro-F1 без подсказки',
  hint_rate: 'доля подсказок',
  hint_precision: 'точность подсказок',
  rows: 'строки',
  systems: 'системы',
  per_class: 'по классам',
  confusion: 'матрица ошибок',
  by_spec: 'по направлениям',
  durations_s: 'время, с',
  total_s: 'всего, с',
  git_sha: 'коммит',
  mode: 'режим',
  bank_families: 'семейств в банке',
  run_config: 'конфигурация прогона',
  conditions: 'условия',
  scenarios: 'сценарии',
  curve: 'кривая',
  precision: 'точность',
  recall: 'полнота',
  f1: 'F1',
  support: 'поддержка',
}

const MAX_DEPTH = 3
const MAX_ROWS = 40
const MAX_COLS = 10

function label(k: string): string {
  return KEY_RU[k] ?? SECTION_RU[k] ?? k
}

function isObj(v: Json): v is Obj {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function scalar(v: Json): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'да' : 'нет'
  if (typeof v === 'number') return Number.isInteger(v) ? formatInt(v) : formatDecimal(v, 3)
  if (typeof v === 'string') return v
  const s = JSON.stringify(v)
  return s.length > 80 ? `${s.slice(0, 77)}…` : s
}

const isScalar = (v: Json): boolean => v === null || ['string', 'number', 'boolean'].includes(typeof v)

function Heading({ children, level }: { children: ReactNode; level: number }) {
  return level <= 1 ? <h2 className="t-name text-ink">{children}</h2> : <h3 className="t-label text-ink-2">{children}</h3>
}

function Node({ name, value, depth }: { name: string; value: Json; depth: number }) {
  if (isScalar(value)) return null
  if (Array.isArray(value)) return <ArrayNode name={name} items={value} depth={depth} />
  const obj = value as Obj
  const entries = Object.entries(obj)
  const scalars = entries.filter(([, v]) => isScalar(v))
  const nested = entries.filter(([, v]) => !isScalar(v))
  if (depth > MAX_DEPTH) {
    return (
      <details className="t-small text-ink-3">
        <summary className="cursor-pointer">{label(name)}: подробности (JSON)</summary>
        <pre className="m-0 mt-2 max-h-72 overflow-auto rounded-panel bg-panel p-3 font-mono text-[12px] text-ink-2">
          {JSON.stringify(value, null, 1)}
        </pre>
      </details>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <Heading level={depth}>{label(name)}</Heading>
      {scalars.length > 0 && (
        <Table head={['Показатель', 'Значение']}>
          {scalars.map(([k, v]) => (
            <tr key={k} className="border-b border-hairline last:border-0">
              <Td className="w-[46%] text-ink-2">{label(k)}</Td>
              <Td mono>{scalar(v)}</Td>
            </tr>
          ))}
        </Table>
      )}
      {nested.length > 0 && (
        <div className="flex flex-col gap-6 border-l border-hairline pl-5">
          {nested.map(([k, v]) => (
            <Node key={k} name={k} value={v} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

function ArrayNode({ name, items, depth }: { name: string; items: Json[]; depth: number }) {
  if (items.length === 0) return null
  if (items.every(isScalar)) {
    return (
      <div className="flex flex-col gap-1.5">
        <Heading level={depth}>{label(name)}</Heading>
        <p className="m-0 break-words font-mono text-[13px] text-ink-2">{items.slice(0, 40).map(scalar).join(' · ')}{items.length > 40 ? ' …' : ''}</p>
      </div>
    )
  }
  const rows = items.filter(isObj)
  if (rows.length === 0) return null
  const cols: string[] = []
  for (const r of rows.slice(0, MAX_ROWS)) {
    for (const k of Object.keys(r)) if (!cols.includes(k) && cols.length < MAX_COLS) cols.push(k)
  }
  return (
    <div className="flex flex-col gap-3">
      <Heading level={depth}>{label(name)}</Heading>
      <Table head={cols.map(label)}>
        {rows.slice(0, MAX_ROWS).map((r, i) => (
          <tr key={i} className="border-b border-hairline last:border-0">
            {cols.map((c) => (
              <Td key={c} mono={typeof r[c] === 'number'}>
                {scalar(r[c])}
              </Td>
            ))}
          </tr>
        ))}
      </Table>
      {rows.length > MAX_ROWS && <p className="t-small text-ink-3">Показаны первые {MAX_ROWS} из {formatInt(rows.length)} строк.</p>}
    </div>
  )
}

/** «Валидация»: последний `make validate` таблицами. Тот же код движков на синтетической популяции, фиксированный seed. */
export default function Validation() {
  const q = useValidationReport()
  return (
    <div className="flex flex-col gap-8">
      <LabHead
        caption="Лаборатория качества теста"
        title="Валидация"
        lead="Проверка движков на синтетической популяции с латентной истиной: тот же код, что в продукте, фиксированные seed. Числа ниже — модельные, не измерения на людях."
      />
      {q.isPending ? (
        <LabSkeleton rows={5} />
      ) : q.isError ? (
        isApiException(q.error) && q.error.code === 'validation_missing' ? (
          <Panel variant="panel" radius="lg">
            <EmptyState
              title="Прогона валидации ещё нет"
              description={
                <>
                  Выполните <code className="font-mono">make validate</code> в каталоге backend — результат появится здесь.
                </>
              }
            />
          </Panel>
        ) : (
          <LabError error={q.error} onRetry={() => void q.refetch()} />
        )
      ) : (
        <Report report={q.data} />
      )}
    </div>
  )
}

function Report({ report }: { report: ValidationReport }) {
  const order = Object.keys(SECTION_RU)
  const sections = report.sections
    .filter((s) => s !== 'meta')
    .sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
  const [tab, setTab] = useState<string>(sections[0] ?? '')
  const meta = report.meta as Obj
  const data = (report.result as Obj)[tab]
  const when = typeof report.file_time === 'string' ? new Date(report.file_time) : null
  return (
    <>
      <Counters
        items={[
          { value: String(meta.seed ?? '—'), label: 'seed' },
          { value: scalar(meta.bank_families), label: 'семейств в банке' },
          { value: typeof meta.total_s === 'number' ? `${formatDecimal(meta.total_s, 0)} с` : '—', label: 'время прогона' },
          { value: typeof meta.git_sha === 'string' ? meta.git_sha.slice(0, 7) : '—', label: 'коммит', tone: 'brand' },
        ]}
      />
      <p className="t-small text-ink-3">
        Источник: {report.source}
        {when && !Number.isNaN(when.getTime()) ? `, файл от ${new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long', timeStyle: 'short' }).format(when)}` : ''}.
      </p>
      <Tabs
        aria-label="Раздел отчёта"
        options={sections.map((s) => ({ value: s, label: SECTION_RU[s] ?? s }))}
        value={tab}
        onChange={setTab}
      />
      <div className="flex flex-col gap-8">
        {isObj(data) || Array.isArray(data) ? <Node name={tab} value={data} depth={1} /> : <p className="t-body text-ink-2">{scalar(data)}</p>}
      </div>
    </>
  )
}
