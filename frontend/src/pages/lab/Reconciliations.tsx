import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useReconciliations, type ReconciliationRow, type ReviewFilter } from '@/features/anticheat/api'
import { formatInt } from '@/lib/format'
import { Alert, Button, EmptyState, Illustration, Panel } from '@/ui'
import { Tabs } from '@/ui/Tabs'
import { Chip, LabError, LabHead, LabSkeleton, Table, Td } from './parts'

const TABS: { value: ReviewFilter; label: string }[] = [
  { value: 'open', label: 'Ждут решения' },
  { value: 'verification_assigned', label: 'Подтверждение назначено' },
  { value: 'dismissed', label: 'Отметка снята' },
  { value: 'all', label: 'Все' },
]

function time(v: string | null | undefined): string {
  if (!v) return '—'
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(v))
}

function statusTone(r: ReconciliationRow): 'neutral' | 'brand' | 'danger' {
  if (r.review_status === 'open') return r.strength === 'exact' ? 'brand' : 'neutral'
  return 'neutral'
}

/** «Сверки»: ответы, в которых есть след копии из буфера обмена. Открывают «Акт сверки» (Figma 39:2). */
export default function Reconciliations() {
  const [tab, setTab] = useState<ReviewFilter>('open')
  const q = useReconciliations(tab)
  const counts = q.data?.counts
  const total = counts ? counts.open + counts.verification_assigned + counts.dismissed : undefined
  const tabs = TABS.map((t) => ({
    ...t,
    count:
      counts === undefined
        ? undefined
        : t.value === 'all'
          ? total
          : counts[t.value as 'open' | 'verification_assigned' | 'dismissed'],
  }))

  return (
    <div className="flex flex-col gap-8">
      <LabHead
        caption="Антиподсказчик · лаборатория качества теста"
        title="Сверки"
        lead="Ответ, который по экрану получить нельзя, а по скопированному тексту — можно. Мы не говорим «использовал ИИ»: это наблюдение о тексте, а решает методист."
        aside={<Illustration name="detector" className="hidden w-[168px] shrink-0 lg:block" alt="" eager />}
      />

      {q.data && <Alert>{q.data.note_ru}</Alert>}

      <Tabs aria-label="Фильтр сверок" options={tabs} value={tab} onChange={setTab} />

      {q.isPending ? (
        <LabSkeleton />
      ) : q.isError ? (
        <LabError error={q.error} onRetry={() => void q.refetch()} />
      ) : q.data.items.length === 0 ? (
        <Panel variant="panel" radius="lg">
          <EmptyState
            image="detector"
            title={tab === 'open' ? 'Сверок, ожидающих решения, нет' : 'В этом списке пока пусто'}
            description={
              <>
                Сверка появляется, когда кандидат отвечает ключом копии. Чтобы увидеть её своими глазами, откройте песочницу
                «Попробуй списать» и скопируйте условие в чат-бот. Двойники для пула считаются заранее — на экране «Банк заданий».
              </>
            }
            action={
              <div className="flex flex-wrap justify-center gap-3">
                <Button to="/try">Попробовать списать</Button>
                <Button variant="outline" to="/lab/bank">
                  Банк заданий
                </Button>
              </div>
            }
          />
        </Panel>
      ) : (
        <Table head={['Сверка', 'Что нашли', 'Задание', 'Момент', 'Решение', 'Когда']}>
          {q.data.items.map((r) => (
            <tr key={r.id} className="border-b border-hairline transition-colors hover:bg-panel/60">
              <Td className="max-w-[320px]">
                <Link
                  to={`/lab/reconciliations/${encodeURIComponent(r.id)}`}
                  className="t-label text-ink no-underline hover:text-brand"
                >
                  {r.label}
                </Link>
              </Td>
              <Td>
                <span className="block">{r.headline_ru}</span>
                <span className="block t-small text-ink-3">
                  ответ <span className="font-mono">{r.answer}</span> · {r.matched_ru}
                </span>
              </Td>
              <Td className="max-w-[220px]">
                <span className="block">{r.family_title}</span>
                <span className="block t-small text-ink-3">вариант #{r.variant_code}</span>
              </Td>
              <Td>{r.phase_ru}</Td>
              <Td>
                <Chip tone={statusTone(r)}>{r.review_status_ru}</Chip>
              </Td>
              <Td className="whitespace-nowrap text-ink-2">{time(r.answered_at)}</Td>
            </tr>
          ))}
        </Table>
      )}

      {q.data && <p className="t-small text-ink-3">Показано сверок: {formatInt(q.data.items.length)}. Решение методиста меняет только метку здесь; работодатель его не видит.</p>}
    </div>
  )
}

