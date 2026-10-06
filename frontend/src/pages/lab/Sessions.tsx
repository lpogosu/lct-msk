import { useState } from 'react'
import { Link } from 'react-router-dom'
import { GRADE_RU, SESSION_STATUS_RU, useLabSessions } from '@/features/anticheat/labApi'
import { formatInt } from '@/lib/format'
import { EmptyState, Panel } from '@/ui'
import { Select } from '@/ui/Select'
import { Chip, LabError, LabHead, LabSkeleton, Table, Td } from './parts'

const STATUSES = ['', 'IN_PROGRESS', 'DECIDED', 'EXTENSION_OFFERED', 'FINAL', 'UNDER_REVIEW', 'ABANDONED', 'INVALIDATED']

function when(v: string | null | undefined): string {
  if (!v) return '—'
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(v))
}

/** «Сессии»: последние тесты, вход в трассу (уровень и погрешность по шагам, «почему это задание»). */
export default function Sessions() {
  const [status, setStatus] = useState('')
  const q = useLabSessions(status, '')
  return (
    <div className="flex flex-col gap-8">
      <LabHead
        caption="Лаборатория качества теста"
        title="Сессии"
        lead="Последние тесты кандидатов. Откройте трассу, чтобы увидеть, как менялись уровень и погрешность по шагам и почему система выбрала именно это задание."
        actions={
          <Select aria-label="Статус" value={status} onChange={(e) => setStatus(e.target.value)} wrapperClassName="min-w-[240px]">
            {STATUSES.map((s) => (
              <option key={s || 'all'} value={s}>
                {s ? SESSION_STATUS_RU[s] : 'Все статусы'}
              </option>
            ))}
          </Select>
        }
      />
      {q.isPending ? (
        <LabSkeleton />
      ) : q.isError ? (
        <LabError error={q.error} onRetry={() => void q.refetch()} />
      ) : q.data.length === 0 ? (
        <Panel variant="panel" radius="lg">
          <EmptyState title="Сессий пока нет" description="Они появятся, когда кандидаты начнут проходить тест." />
        </Panel>
      ) : (
        <Table head={['Сессия', 'Статус', 'Грейд', 'Заданий', 'Балл', 'Доверие', 'Флаги', 'Начало']}>
          {q.data.map((s) => (
            <tr key={s.id} className="border-b border-hairline transition-colors hover:bg-panel/60">
              <Td>
                <Link to={`/lab/sessions/${s.id}`} className="t-label text-ink no-underline hover:text-brand">
                  {s.id.slice(0, 8)}
                </Link>
                {s.demo_assisted && (
                  <span className="ml-2">
                    <Chip tone="brand">demo_assisted</Chip>
                  </span>
                )}
                <span className="block t-small text-ink-3">{s.specialization}</span>
              </Td>
              <Td>
                <Chip>{SESSION_STATUS_RU[s.status] ?? s.status}</Chip>
                {s.decision && <span className="mt-1 block t-small text-ink-3">{s.decision}</span>}
              </Td>
              <Td>{GRADE_RU[s.claimed_grade] ?? s.claimed_grade}</Td>
              <Td mono>{s.n_items}</Td>
              <Td mono>{s.score ?? '—'}</Td>
              <Td mono>{s.trust == null ? '—' : s.trust.toFixed(2).replace('.', ',')}</Td>
              <Td mono className={s.flags_open > 0 ? 'text-brand' : undefined}>
                {formatInt(s.flags_open)}
              </Td>
              <Td className="whitespace-nowrap text-ink-2">{when(s.started_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  )
}
