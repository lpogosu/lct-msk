import { useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { qk } from '@/features/candidate/queries'
import { ItemPlayer, type AnswerValue, type TestItem } from '@/features/candidate/test/ItemPlayer'
import { Alert, Button, MobileHeader, Panel, TextArea } from '@/ui'

type Started = Schemas['AssignmentStartOut']
type Outcome = Schemas['OutcomeOut']

/**
 * Плеер задачи (К11): тип A — одноразовый экземпляр из банка (тот же плеер, что у теста, автопроверка),
 * тип B — «предложи подход»: текст по рубрике из 4 критериев; решение уходит компании без имени.
 */
export default function CandidateTaskPlayer() {
  const { assignmentId = '' } = useParams()
  const q = useQuery({
    queryKey: ['candidate', 'task-start', assignmentId],
    queryFn: () => unwrap(api.POST('/api/tasks/assignments/{assignment_id}/start', { params: { path: { assignment_id: assignmentId } } })),
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  })
  return (
    <>
      <MobileHeader title="Задача" backTo="/candidate/tasks" />
      <QueryBoundary query={q} errorTitle="Задачу открыть не удалось">
        {(a) => <Player a={a} />}
      </QueryBoundary>
    </>
  )
}

function Player({ a }: { a: Started }) {
  const qc = useQueryClient()
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [text, setText] = useState('')
  const answerRef = useRef<() => AnswerValue>(() => null)

  const submit = useMutation({
    mutationFn: (answer: unknown) =>
      unwrap(api.POST('/api/tasks/assignments/{assignment_id}/submit', { params: { path: { assignment_id: a.id } }, body: { answer } })),
    onSuccess: (d) => {
      setOutcome(d.outcome)
      void qc.invalidateQueries({ queryKey: qk.tasks })
      void qc.invalidateQueries({ queryKey: qk.qualifications })
    },
  })

  if (outcome) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="t-title text-brand">{outcome.correct === false ? 'Не получилось' : outcome.correct ? 'Верно' : 'Отправлено'}</h1>
        <p className="t-lead text-ink">{outcome.message}</p>
        {outcome.timed_out && <Alert>Время вышло: ответ не засчитан.</Alert>}
        {outcome.correct_answer != null && (
          <Panel variant="panel" radius="lg" padding="sm">
            <p className="t-small text-ink-3">Верный ответ вашего экземпляра</p>
            <p className="whitespace-pre-wrap break-words font-mono text-[14px] text-brand">
              {typeof outcome.correct_answer === 'string' ? outcome.correct_answer : JSON.stringify(outcome.correct_answer)}
            </p>
          </Panel>
        )}
        {outcome.explanation && <p className="whitespace-pre-line t-small text-ink-2">{outcome.explanation}</p>}
        {outcome.misconception && <Alert title="Что выдаёт ваш ответ">{outcome.misconception}</Alert>}
        {outcome.upgrade_hint && <Alert>{outcome.upgrade_hint}</Alert>}
        {outcome.paused_until_text && <p className="t-small text-ink-3">{outcome.paused_until_text}</p>}
        <Button block size="lg" to="/candidate/tasks">
          К задачам
        </Button>
      </div>
    )
  }

  const maxLen = a.answer_max_len ?? 3000

  return (
    <div className="flex flex-col gap-6">
      <header>
        <p className="t-small text-ink-3">
          {a.origin.label} · {a.minutes} мин
        </p>
        <h1 className="mt-1 t-title text-brand">{a.title}</h1>
        <p className="mt-2 t-small text-ink-3">{a.not_real_work_label}</p>
        {a.solve_until_text && <p className="mt-1 t-small text-danger-text">Решить {a.solve_until_text}</p>}
      </header>

      {a.kind === 'catalog' && a.item ? (
        <ItemPlayer
          item={toItem(a.item)}
          submitting={submit.isPending}
          answerRef={answerRef}
          onSubmit={(v) => submit.mutate(v)}
        />
      ) : (
        <div className="flex flex-col gap-5">
          {a.prompt && <p className="whitespace-pre-line t-lead text-ink">{a.prompt}</p>}
          {a.rubric?.criteria != null ? (
            <Panel variant="panel" radius="lg" padding="sm">
              <p className="mb-1.5 t-label text-ink">Как оценим (каждый критерий — от 0 до {String(a.rubric.max_points ?? 2)})</p>
              <ul className="flex flex-col gap-1 t-small text-ink-2">
                {Object.values(a.rubric.criteria as Record<string, string>).map((c) => (
                  <li key={c}>· {c}</li>
                ))}
              </ul>
            </Panel>
          ) : null}
          <TextArea
            label="Ваш подход"
            value={text}
            onChange={(e) => setText(e.target.value.slice(0, maxLen))}
            rows={8}
            hint={`${text.length}/${maxLen}. Без контактов и ссылок: решение уйдёт компании без вашего имени.`}
            data-answer-field
          />
          <Button block size="lg" disabled={text.trim().length < 20} loading={submit.isPending} onClick={() => submit.mutate(text.trim())}>
            Отправить решение
          </Button>
        </div>
      )}

      {submit.error ? (
        <Alert tone="danger" title="Не удалось отправить">
          {errorMessage(submit.error)}
        </Alert>
      ) : null}
    </div>
  )
}

/** Публичный экземпляр задачи из каталога приходит словарём — приводим к форме задания теста. */
function toItem(raw: Record<string, unknown>): TestItem {
  return {
    seq: 0,
    variant_code: String(raw.variant_code ?? ''),
    prompt: String(raw.prompt ?? ''),
    code: (raw.code as string | null | undefined) ?? null,
    language: (raw.language as string | null | undefined) ?? null,
    options: (raw.options as TestItem['options']) ?? [],
    lines: (raw.lines as string[] | undefined) ?? [],
    table: (raw.table as TestItem['table']) ?? null,
    answer_mode: String(raw.answer_mode ?? 'choice'),
    answer_hint: (raw.answer_hint as string | null | undefined) ?? null,
    answer_format: '',
    time_limit_s: 0,
    deadline_at: '',
    seconds_left: 0,
    clip: null,
  }
}
