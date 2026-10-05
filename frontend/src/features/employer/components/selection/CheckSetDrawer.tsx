import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { formatDecimal } from '@/lib/format'
import { Alert, Button, Checkbox, Skeleton, useToast } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { useNeedCheck, type ShortlistEntry } from '../../api'
import { BlockTitle, LoadError, Tag } from '../common'

const MAX_CANDIDATES = 30 // TasksCheckSetIn: candidates ≤ 30

/**
 * «Проверочный набор по вакансии» (DECISIONS 3, концепция §5.5): обязательные навыки брифа → модули теста →
 * 3–5 откалиброванных семейств, не дольше 15 минут. Каждому из шортлиста уйдут свои одноразовые экземпляры
 * («два кандидата — два теста»). Стандартный тест и грейд не меняются, результат подтверждает модули в подборке.
 */
export function CheckSetDrawer({ needId, entries, onClose }: { needId: string; entries: ShortlistEntry[]; onClose: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const check = useNeedCheck(needId)
  const [picked, setPicked] = useState<Set<string>>(() => new Set(entries.map((e) => e.ref)))
  const results = useQuery({
    queryKey: ['employer', 'check-set-results', needId],
    queryFn: () => unwrap(api.GET('/api/tasks/check-sets/{need_id}', { params: { path: { need_id: needId } } })),
  })
  const send = useMutation({
    mutationFn: () => unwrap(api.POST('/api/tasks/check-sets', { body: { need_id: needId, candidates: [...picked].slice(0, MAX_CANDIDATES) } })),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['employer', 'check-set-results', needId] })
      void qc.invalidateQueries({ queryKey: ['employer', 'tasks'] })
      toast.success(`Проверочный набор отправлен: ${r.sent.length}${r.rejected.length ? `, не отправлен: ${r.rejected.length}` : ''}`)
    },
  })

  const plan = check.data?.check_set
  return (
    <Drawer
      open
      onClose={onClose}
      width={600}
      eyebrow="Шортлист"
      title="Проверочный набор"
      footer={
        <div className="flex flex-wrap gap-4">
          <Button loading={send.isPending} disabled={picked.size === 0 || !plan || plan.slots.length === 0} onClick={() => send.mutate()}>
            Отправить набор ({picked.size})
          </Button>
          <Button variant="outline" onClick={onClose}>
            Закрыть
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-7">
        <section className="flex flex-col gap-3" aria-label="План набора">
          <BlockTitle>Что получит каждый кандидат</BlockTitle>
          {check.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : check.isError ? (
            <LoadError error={check.error} onRetry={() => void check.refetch()} />
          ) : plan && plan.slots.length > 0 ? (
            <>
              <ul className="m-0 flex list-none flex-col divide-y divide-hairline border-y border-hairline p-0">
                {plan.slots.map((s) => (
                  <li key={s.module} className="flex flex-wrap items-baseline justify-between gap-x-4 py-2.5">
                    <span className="t-body text-ink">
                      {s.module_name} <span className="text-ink-3">— {s.skills.join(', ')}</span>
                    </span>
                    <span className="t-small text-ink-3">{formatDecimal(s.minutes, 1)} мин</span>
                  </li>
                ))}
              </ul>
              <p className="t-small text-ink-3">
                Всего около {formatDecimal(plan.total_minutes, 1)} мин. У каждого кандидата свои одноразовые экземпляры заданий; на решение — 3 дня.
                Стандартный тест и грейд не меняются.
              </p>
              {check.data && check.data.untestable.length > 0 && (
                <p className="t-small text-ink-3">
                  Не проверяются тестом, спросите на интервью: {check.data.untestable.map((u) => u.name).join(', ')}.
                </p>
              )}
            </>
          ) : (
            <Alert tone="info" title="Набор собрать не из чего">
              {plan?.note ?? 'Обязательные навыки брифа тестом не проверяются — проверьте их на интервью.'}
            </Alert>
          )}
        </section>

        <section className="flex flex-col gap-2" aria-label="Кому отправить">
          <BlockTitle>Кому отправить</BlockTitle>
          {entries.length === 0 ? (
            <p className="t-body text-ink-3">В шортлисте пока никого.</p>
          ) : (
            entries.map((e) => (
              <Checkbox
                key={e.ref}
                checked={picked.has(e.ref)}
                onChange={(ev) =>
                  setPicked((cur) => {
                    const next = new Set(cur)
                    if (ev.target.checked) next.add(e.ref)
                    else next.delete(e.ref)
                    return next
                  })
                }
                label={
                  <span>
                    {e.pseudonym}
                    {e.mark === 'yellow' && <span className="ml-2 t-small text-ink-3">не в текущей версии</span>}
                  </span>
                }
              />
            ))
          )}
        </section>

        {send.isError && (
          <Alert tone="danger" title="Набор не отправлен">
            {errorMessage(send.error)}
          </Alert>
        )}
        {send.data && send.data.rejected.length > 0 && (
          <section className="flex flex-col gap-1" aria-label="Не отправлено">
            <BlockTitle>Не отправлено</BlockTitle>
            {send.data.rejected.map((r) => (
              <p key={r.pseudonym} className="t-small text-ink-2">
                {r.pseudonym}: {r.message}
              </p>
            ))}
          </section>
        )}

        <section className="flex flex-col gap-2" aria-label="Результаты">
          <BlockTitle>Результаты</BlockTitle>
          {results.isPending ? (
            <Skeleton className="h-12 w-full" />
          ) : results.isError ? (
            <p className="t-small text-danger-text">{errorMessage(results.error)}</p>
          ) : (results.data?.candidates ?? []).length === 0 ? (
            <p className="t-small text-ink-3">Набор ещё никому не отправляли.</p>
          ) : (
            <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
              {results.data?.candidates.map((c) => (
                <li key={c.pseudonym} className="py-2.5">
                  <p className="flex flex-wrap items-center gap-2 t-label text-ink">
                    {c.pseudonym}
                    <Tag tone={c.done === c.total && c.total > 0 ? 'brand' : 'default'}>
                      решено {c.done} из {c.total}, верно {c.correct}
                    </Tag>
                  </p>
                  <p className="t-small text-ink-3">{c.items.map((i) => `${i.module_name ?? i.title}: ${i.status_label}`).join(' · ')}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Drawer>
  )
}
