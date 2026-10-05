import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import { useTaskMutations, useTasks, useTaskSubmissions, type Submission, type TaskOut } from '@/features/employer/api'
import { InviteDrawer } from '@/features/employer/components/InviteDrawer'
import { BlockTitle, LoadError, PageHead, PageSkeleton, Tag } from '@/features/employer/components/common'
import { ProfileGate } from '@/features/employer/components/ProfileGate'
import { NewTaskDrawer, ReviewDrawer } from '@/features/employer/components/tasks/TaskDrawers'
import { formatDecimal } from '@/lib/format'
import { Button, Counters, EmptyState, Identicon, Skeleton, useToast } from '@/ui'

const ACTIVE_MAX = 5 // config/contact.yaml: не больше 5 активных задач

const STATUS_LABEL: Record<string, string> = { active: 'идёт', paused: 'на паузе', closed: 'закрыта' }

/** Р11 «Задачи для кандидатов»: каталог откалиброванных семейств, «предложи подход», обезличенные решения и «пригласить». */
export default function Tasks() {
  const toast = useToast()
  const tasks = useTasks()
  const { status } = useTaskMutations()
  const [creating, setCreating] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [openTask, setOpenTask] = useState<string | null>(null)

  const list = (tasks.data ?? []).filter((t) => !t.is_check_set)
  const active = list.filter((t) => t.status === 'active').length

  async function change(t: TaskOut, action: 'pause' | 'activate' | 'close') {
    setBusyId(t.id)
    try {
      await status.mutateAsync({ id: t.id, action })
      toast.success(action === 'pause' ? 'Задача на паузе' : action === 'activate' ? 'Задача возобновлена' : 'Задача закрыта')
    } catch (e) {
      toast.error(errorMessage(e), 'Статус не изменился')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="flex flex-col gap-10">
      <PageHead
        title="Задачи для кандидатов"
        lead="Короткие задачи подтверждают модули теста и свежесть профиля. Решения обезличены: вы видите псевдоним и результат — и можете пригласить тех, кто решил хорошо."
        actions={
          <Button onClick={() => setCreating(true)} disabled={active >= ACTIVE_MAX}>
            Новая задача
          </Button>
        }
      />
      <ProfileGate />

      {tasks.isPending ? (
        <PageSkeleton rows={3} />
      ) : tasks.isError ? (
        <LoadError error={tasks.error} onRetry={() => void tasks.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState
          title="Нет активных задач"
          description="Выберите откалиброванное семейство из каталога или опишите свою задачу «предложи подход». Не больше пяти активных, до 15 минут каждая."
          action={<Button onClick={() => setCreating(true)}>Новая задача</Button>}
        />
      ) : (
        <>
          <Counters
            items={[
              { value: `${active}/${ACTIVE_MAX}`, label: 'активных задач' },
              { value: list.reduce((s, t) => s + t.stats.offered, 0), label: 'предложено кандидатам' },
              { value: list.reduce((s, t) => s + t.stats.submitted, 0), label: 'решений получено' },
              {
                value: list.reduce((s, t) => s + t.stats.awaiting_review, 0),
                label: 'ждут вашей оценки',
                tone: list.some((t) => t.stats.awaiting_review > 0) ? 'brand' : 'default',
              },
            ]}
          />
          {active >= ACTIVE_MAX && <p className="t-small text-ink-3">Достигнут лимит активных задач — поставьте одну на паузу или закройте.</p>}
          <ul className="m-0 flex list-none flex-col border-t border-hairline p-0">
            {list.map((t) => (
              <li key={t.id} className="border-b border-hairline py-5">
                <div className="grid gap-x-8 gap-y-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="t-name text-ink [overflow-wrap:anywhere]">{t.title}</p>
                      <Tag tone={t.status === 'active' ? 'brand' : 'default'}>{STATUS_LABEL[t.status] ?? t.status}</Tag>
                      <Tag>{t.kind === 'catalog' ? 'тип A · каталог' : 'тип B · подход'}</Tag>
                      {t.stats.awaiting_review > 0 && <Tag tone="brand">ждут оценки: {t.stats.awaiting_review}</Tag>}
                    </div>
                    <p className="mt-1 t-body text-ink-2">
                      {t.audience.specialization_name} · {t.audience.grade_name}
                      {t.audience.window ? ' ± 1' : ''} · {t.minutes} мин{t.module_name ? ` · модуль «${t.module_name}»` : ''}
                    </p>
                    <p className="t-small text-ink-3">
                      Предложено {t.stats.offered} · решено {t.stats.submitted} · пропущено {t.stats.skipped}
                      {t.stats.avg_score != null ? ` · средний результат ${formatDecimal(t.stats.avg_score * 100, 0)}%` : ''}
                    </p>
                    <p className="t-small text-ink-3">{t.not_real_work_label}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                    <Button variant="outline" size="sm" aria-expanded={openTask === t.id} onClick={() => setOpenTask(openTask === t.id ? null : t.id)}>
                      {openTask === t.id ? 'Скрыть решения' : 'Решения'}
                    </Button>
                    {t.status === 'active' && (
                      <Button variant="text" loading={busyId === t.id} onClick={() => void change(t, 'pause')}>
                        Пауза
                      </Button>
                    )}
                    {t.status === 'paused' && (
                      <Button variant="text" loading={busyId === t.id} disabled={active >= ACTIVE_MAX} onClick={() => void change(t, 'activate')}>
                        Возобновить
                      </Button>
                    )}
                    {t.status !== 'closed' && (
                      <Button variant="text" disabled={busyId === t.id} onClick={() => void change(t, 'close')}>
                        Закрыть
                      </Button>
                    )}
                  </div>
                </div>
                {openTask === t.id && <Submissions task={t} />}
              </li>
            ))}
          </ul>
        </>
      )}

      {creating && <NewTaskDrawer onClose={() => setCreating(false)} />}
    </div>
  )
}

function Submissions({ task }: { task: TaskOut }) {
  const q = useTaskSubmissions(task.id)
  const [review, setReview] = useState<Submission | null>(null)
  const [invite, setInvite] = useState<Submission | null>(null)

  return (
    <div className="mt-4 rounded-panel bg-panel p-4">
      <BlockTitle>Решения (обезличены)</BlockTitle>
      {q.isPending ? (
        <Skeleton className="mt-3 h-16 w-full" />
      ) : q.isError ? (
        <LoadError className="mt-3" error={q.error} onRetry={() => void q.refetch()} />
      ) : (q.data ?? []).length === 0 ? (
        <p className="mt-2 t-body text-ink-3">Пока никто не решил. Задача предлагается кандидатам не чаще раза в неделю.</p>
      ) : (
        <ul className="m-0 mt-2 flex list-none flex-col divide-y divide-hairline p-0">
          {q.data?.map((s) => (
            <li key={s.assignment_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
              <Identicon seed={s.pseudonym} size={28} />
              <div className="min-w-0 flex-1">
                <p className="t-label text-ink">
                  {s.pseudonym} <span className="font-normal text-ink-3">· {s.status_label}</span>
                </p>
                <p className="t-small text-ink-2">
                  {s.score_text ?? (s.is_correct == null ? '' : s.is_correct ? 'верно' : 'неверно')}
                  {s.review_comment ? ` · «${s.review_comment}»` : ''}
                </p>
                {task.kind === 'approach' && s.answer != null && <p className="line-clamp-2 t-small text-ink-3">{String(s.answer)}</p>}
              </div>
              <div className="flex items-center gap-4">
                {s.can_review && (
                  <Button variant="text" onClick={() => setReview(s)}>
                    Оценить
                  </Button>
                )}
                {s.candidate_ref && (
                  <Button variant="text" onClick={() => setInvite(s)}>
                    Пригласить
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {review && <ReviewDrawer task={task} submission={review} onClose={() => setReview(null)} />}
      <InviteDrawer
        open={!!invite}
        onClose={() => setInvite(null)}
        need={null}
        candidates={invite?.candidate_ref ? [{ ref: invite.candidate_ref, pseudonym: invite.pseudonym, whyYou: [] }] : []}
      />
    </div>
  )
}
