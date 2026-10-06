import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { errorMessage } from '@/api/errors'
import { ActDetails, ActView } from '@/features/anticheat/ActView'
import { useAct, useLabKit, useReview, type Act, type ReviewAction } from '@/features/anticheat/api'
import { Alert, Button, Panel, TextArea, useToast } from '@/ui'
import { Chip, LabError, LabSkeleton } from './parts'

/**
 * «Акт сверки» (Figma 39:2): показано и скопировано рядом, оба ключа, ответ, четыре числа, «Что это значит» и два решения
 * методиста. Автобана нет: «Назначить подтверждение» и «Снять отметку» — человеческие и обратимые решения.
 */
export default function ReconciliationAct() {
  const { id = '' } = useParams()
  const q = useAct(id)
  return (
    <div className="flex flex-col gap-6">
      <nav aria-label="Навигация" className="t-small text-ink-3">
        <Link to="/lab/reconciliations" className="text-ink-2 underline underline-offset-4 hover:text-brand">
          Сверки
        </Link>{' '}
        / акт сверки
      </nav>
      {q.isPending ? <LabSkeleton rows={6} /> : q.isError ? <LabError error={q.error} onRetry={() => void q.refetch()} /> : <ActBody act={q.data} id={id} />}
    </div>
  )
}

function ActBody({ act, id }: { act: Act; id: string }) {
  const toast = useToast()
  const review = useReview(id)
  const [note, setNote] = useState('')
  const [showKit, setShowKit] = useState(false)

  async function decide(action: ReviewAction, done: string) {
    try {
      await review.mutateAsync({ action, note })
      setNote('')
      toast.success(done)
    } catch (e) {
      toast.error(errorMessage(e), 'Решение не сохранено')
    }
  }

  const open = act.review_status === 'open'
  const actions = open ? (
    <>
      <Button loading={review.isPending} onClick={() => void decide('assign_verification', 'Подтверждение назначено')}>
        Назначить подтверждение
      </Button>
      <Button variant="outline" disabled={review.isPending} onClick={() => void decide('dismiss', 'Отметка снята')}>
        Снять отметку
      </Button>
    </>
  ) : (
    <Button variant="outline" loading={review.isPending} onClick={() => void decide('reopen', 'Вернули в очередь')}>
      Вернуть в очередь
    </Button>
  )

  return (
    <>
      <ActView act={act} actions={actions}>
        <div className="flex flex-col gap-3">
          {!open && (
            <Alert title={act.review_status_ru[0].toUpperCase() + act.review_status_ru.slice(1)}>
              {act.review_note ?? 'Решение принято методистом.'} Работодатель решение и причины не видит: только итоговый уровень доверия.
            </Alert>
          )}
          {open && (
            <TextArea
              label="Комментарий к решению (необязательно)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              rows={2}
              hint="Остаётся у методистов. Кандидат увидит только нейтральную фразу о несовпадении и приглашение на подтверждение."
            />
          )}
        </div>
        <ActDetails act={act} />
        {act.demo_assisted && (
          <p className="t-small text-ink-3">
            <Chip tone="brand">demo_assisted</Chip> Сессия жюри: в метрики теста не входит.
          </p>
        )}
      </ActView>

      {act.session_id && (
        <Panel variant="tint" radius="lg" className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="t-name text-ink">Подтверждение за 5 минут</h2>
              <p className="t-body text-ink-2">
                Скриншот двойника не ловит. Против него — три вопроса по собственным заданиям кандидата с одним изменённым числом.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => setShowKit((v) => !v)} aria-expanded={showKit}>
              {showKit ? 'Скрыть комплект' : 'Показать комплект'}
            </Button>
          </div>
          {showKit && <Kit sessionId={act.session_id} />}
        </Panel>
      )}
    </>
  )
}

function Kit({ sessionId }: { sessionId: string }) {
  const q = useLabKit(sessionId, true)
  if (q.isPending) return <LabSkeleton rows={3} />
  if (q.isError) return <Alert tone="danger">{errorMessage(q.error)}</Alert>
  return (
    <ol className="m-0 flex list-none flex-col gap-5 p-0">
      {q.data.questions.map((x) => (
        <li key={x.id} className="flex flex-col gap-2 rounded-panel bg-white p-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[13px] text-ink-3">{x.id}</span>
            {x.variant_code && <Chip>вариант #{x.variant_code}</Chip>}
            {x.selection_reason && <Chip tone="brand">{x.selection_reason}</Chip>}
            {x.label_ru && <Chip>{x.label_ru}</Chip>}
          </div>
          <p className="t-body text-ink">{x.prompt_ru}</p>
          {x.expected_answer && (
            <p className="t-small text-ink-2">
              Ожидаемый ответ ({x.verified_by === 'execution' ? 'получен исполнением' : 'по генератору'}):{' '}
              <span className="font-mono text-ink" translate="no">
                {x.expected_answer}
              </span>
            </p>
          )}
          <p className="t-small text-ink-3">Что слушать: {x.listen_for_ru}</p>
          <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 t-small text-ink-3">
            {x.rubric.map((r) => (
              <li key={r.score}>
                <span className="font-mono text-ink-2">{r.score}</span> — {r.text}
              </li>
            ))}
          </ul>
        </li>
      ))}
      {q.data.shortfall > 0 && <li className="t-small text-ink-3">Вопросов по трассе не хватило: {q.data.shortfall}.</li>}
    </ol>
  )
}
