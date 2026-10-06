import { Link, useParams } from 'react-router-dom'
import { useTwinCoverage } from '@/features/anticheat/api'
import { useClones } from '@/features/anticheat/labApi'
import { formatDecimal } from '@/lib/format'
import { Alert, Panel } from '@/ui'
import { Chip, LabError, LabHead, LabSkeleton } from './parts'

function keyText(k: unknown): string {
  if (k === null || k === undefined) return '—'
  return typeof k === 'string' ? k : JSON.stringify(k)
}

function TableView({ table }: { table: Record<string, unknown> }) {
  const columns = Array.isArray(table.columns) ? (table.columns as unknown[]) : []
  const rows = Array.isArray(table.rows) ? (table.rows as unknown[][]) : []
  return (
    <div className="overflow-x-auto" translate="no">
      <p className="mb-1 font-mono text-[12px] text-ink-3">{String(table.name ?? '')}</p>
      <table className="border-collapse font-mono text-[13px]">
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th key={i} className="border-b border-hairline px-2 py-1 text-left font-medium text-ink-3">
                {String(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className="px-2 py-0.5">
                  {v === null ? 'NULL' : String(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** «3 клона рядом»: код и ключ у клонов разные, сложность одна на семейство. Клоны вне боевого пула — ключи безопасны. */
export default function Clones() {
  const { familyId = '' } = useParams()
  const q = useClones(familyId, 3)
  const cov = useTwinCoverage()
  const twin = cov.data?.families.find((f) => f.family_id === familyId)
  return (
    <div className="flex flex-col gap-8">
      <nav aria-label="Навигация" className="t-small text-ink-3">
        <Link to="/lab/bank" className="text-ink-2 underline underline-offset-4 hover:text-brand">
          Банк заданий
        </Link>{' '}
        / клоны
      </nav>
      {q.isPending ? (
        <LabSkeleton rows={4} />
      ) : q.isError ? (
        <LabError error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <>
          <LabHead
            caption={familyId}
            title="3 клона рядом"
            lead={
              <>
                {q.data.title}. Сложность одна на все клоны
                {q.data.difficulty != null ? ` (${formatDecimal(q.data.difficulty, 2)})` : ''}, а данные, код и ключ у каждого свои. Это клоны из отдельного
                диапазона: кандидатам они не выдаются.
              </>
            }
          />
          {twin && (
            <Alert title="Двойник в буфере">
              {twin.mechanism === 'own'
                ? 'У семейства свой двойник: ключ считает решатель по показанному тексту.'
                : twin.mechanism === 'generic'
                  ? 'Двойник строит общий мутатор: меняется один литерал данных, ключ получен исполнением.'
                  : (twin.reason_ru ?? 'Для этого формата двойник не строится.')}
            </Alert>
          )}
          <div className="grid gap-5 lg:grid-cols-3">
            {q.data.clones.map((c) => (
              <Panel key={c.seed} variant="outline" radius="lg" className="flex min-w-0 flex-col gap-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone="brand">вариант #{c.variant_code}</Chip>
                  <Chip>{c.answer_mode}</Chip>
                </div>
                <p className="t-body text-ink">{c.prompt}</p>
                {c.code && (
                  <pre translate="no" className="m-0 overflow-x-auto rounded-panel bg-code-bg px-4 py-3 t-code text-white">
                    {c.code}
                  </pre>
                )}
                {c.table && <TableView table={c.table} />}
                {c.options && c.options.length > 0 && (
                  <ul className="m-0 flex list-none flex-col gap-1 p-0 t-small text-ink-2">
                    {c.options.map((o) => (
                      <li key={o.id}>
                        <span className="font-mono">{o.id}</span> · {o.text}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="flex flex-col gap-1 border-t border-hairline pt-3">
                  <p className="t-small text-ink-3">Ключ</p>
                  <p className="break-all t-num-m text-brand" translate="no">
                    {keyText(c.key)}
                  </p>
                  {c.distractor_tags && c.distractor_tags.length > 0 && (
                    <p className="t-small text-ink-3">Заблуждения: {c.distractor_tags.join(', ')}</p>
                  )}
                </div>
              </Panel>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
