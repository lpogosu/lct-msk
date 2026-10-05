import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { Chip, SectionTitle } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { qk, useTasks } from '@/features/candidate/queries'
import { Alert, Button, EmptyState, MobileHeader, Panel, useToast } from '@/ui'

type Assignment = Schemas['AssignmentOut']

/**
 * К11 «Задачи»: предложения (компания или «тренировочная задача платформы», время, на что влияет), «решить / пропустить»
 * и история. Это не рабочие задания компании; пропуск не штрафуется.
 */
export default function CandidateTasks() {
  const q = useTasks()
  return (
    <>
      <MobileHeader title="Задачи" backTo="/candidate/profile" />
      <QueryBoundary query={q}>
        {(t) => (
          <div className="flex flex-col gap-8">
            <header>
              <h1 className="t-title text-brand">Задачи</h1>
              <p className="mt-2 t-body text-ink-2">
                Короткие задания: поддерживают свежесть профиля и подтверждают модули теста. Грейд напрямую не меняют.
              </p>
            </header>

            {t.paused_until_text && <Alert title="Предложения на паузе">{t.paused_until_text}. Отказ не штрафуется.</Alert>}

            <section aria-labelledby="act-h">
              <SectionTitle>
                <span id="act-h">Предложены вам</span>
              </SectionTitle>
              {t.active.length === 0 ? (
                <EmptyState
                  size="compact"
                  title="Задач пока нет"
                  description={t.empty_text ?? 'Новое предложение появляется не чаще раза в 7 дней.'}
                />
              ) : (
                <ul className="flex flex-col gap-3">
                  {t.active.map((a) => (
                    <li key={a.id}>
                      <ActiveCard a={a} />
                    </li>
                  ))}
                </ul>
              )}
              {t.next_offer_text && <p className="mt-3 t-small text-ink-3">Следующее предложение — {t.next_offer_text}.</p>}
              <p className="mt-2 t-small text-ink-3">
                Не чаще одного предложения в {t.limits.offer_interval_days} дней, не больше {t.limits.active_max} активных. После {t.limits.skip_streak} пропусков
                подряд — пауза {t.limits.skip_pause_days} дней.
              </p>
            </section>

            {t.history.length > 0 && (
              <section aria-labelledby="hist-h">
                <SectionTitle>
                  <span id="hist-h">История</span>
                </SectionTitle>
                <ul className="flex flex-col gap-2">
                  {t.history.map((a) => (
                    <li key={a.id} className="flex items-baseline justify-between gap-3 border-b border-hairline pb-2">
                      <span className="min-w-0 t-body text-ink">
                        {a.title}
                        <span className="block t-small text-ink-3">
                          {a.origin.label}
                          {a.result?.score_text ? ` · ${a.result.score_text}` : ''}
                          {a.result?.is_correct != null ? (a.result.is_correct ? ' · верно' : ' · неверно') : ''}
                        </span>
                      </span>
                      <Chip tone="panel">{a.status_label}</Chip>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </QueryBoundary>
    </>
  )
}

function ActiveCard({ a }: { a: Assignment }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  const skip = useMutation({
    mutationFn: () => unwrap(api.POST('/api/tasks/assignments/{assignment_id}/skip', { params: { path: { assignment_id: a.id } } })),
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: qk.tasks })
      toast.show({ message: d.outcome.message })
    },
    onError: (e) => setError(errorMessage(e)),
  })
  return (
    <Panel variant="outline" radius="lg" padding="sm" className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-3">
        <p className="t-body-strong text-ink">{a.title}</p>
        <Chip tone="panel">{a.minutes} мин</Chip>
      </div>
      <p className="t-small text-ink-2">
        {a.origin.label}
        {a.module_name ? ` · модуль «${a.module_name}»` : ''}
      </p>
      <p className="t-small text-ink-3">{a.effect}</p>
      {a.due_text && <p className="t-small text-danger-text">Решить {a.due_text}</p>}
      <p className="t-small text-ink-3">{a.not_real_work_label}</p>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="flex flex-wrap items-center gap-3 pt-1">
        <Button size="sm" to={`/candidate/tasks/${a.id}`}>
          {a.status === 'opened' ? 'Продолжить' : 'Решить'}
        </Button>
        <Button size="sm" variant="text" loading={skip.isPending} onClick={() => skip.mutate()}>
          Пропустить
        </Button>
      </div>
    </Panel>
  )
}
