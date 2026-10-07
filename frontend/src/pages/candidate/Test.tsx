import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import { Sheet } from '@/features/candidate/components/Sheet'
import { ItemPlayer, type AnswerValue } from '@/features/candidate/test/ItemPlayer'
import { useClipboardTwin } from '@/features/anticheat'
import { useTestEvents } from '@/features/candidate/test/useTestEvents'
import { qk } from '@/features/candidate/queries'
import { useCountdown } from '@/hooks/useCountdown'
import { cn } from '@/lib/cn'
import { formatClock } from '@/lib/format'
import { Alert, Button, EmptyState, MobileHeader, Panel, Skeleton, TraceProgress, useToast } from '@/ui'

function subscribeOnline(cb: () => void) {
  window.addEventListener('online', cb)
  window.addEventListener('offline', cb)
  return () => {
    window.removeEventListener('online', cb)
    window.removeEventListener('offline', cb)
  }
}
const useOnline = () => useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true)

/**
 * К4 «Тест»: одно задание на экран, серверный таймер (дедлайн приходит с сервера, часы браузера не авторитет),
 * «задание N из примерно 12–15», код варианта, без кнопки «назад». Журнал событий страницы уходит на бэкенд.
 * Обрыв связи — тест на паузе: возврат возможен в течение 30 минут, задание и его дедлайн остаются прежними.
 */
export default function CandidateTest() {
  const { sessionId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const online = useOnline()

  const [advancing, setAdvancing] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [exitOpen, setExitOpen] = useState(false)
  const answerRef = useRef<() => AnswerValue>(() => null)
  const autoSent = useRef<number | null>(null)

  const next = useQuery({
    queryKey: ['candidate', 'next', sessionId],
    queryFn: () => unwrap(api.GET('/api/tests/sessions/{session_id}/next', { params: { path: { session_id: sessionId } } })),
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnReconnect: true,
  })
  const { flush } = useTestEvents(sessionId)

  // «Режим жюри» (только DEMO_MODE): подсказка к заданию, сессия помечается demo_assisted и в метрики не попадает
  const demo = useQuery({
    queryKey: ['candidate', 'demo-state'],
    queryFn: () => unwrap(api.GET('/api/demo/state')),
    retry: false,
    staleTime: 30_000,
  })
  const [hint, setHint] = useState<{ seq: number; answer: unknown } | null>(null)
  const askHint = useMutation({
    mutationFn: () => unwrap(api.GET('/api/demo/jury/hint', { params: { query: { session_id: sessionId } } })),
    onSuccess: (h) => setHint({ seq: h.seq, answer: h.answer }),
    onError: (e) => toast.error(errorMessage(e)),
  })

  const item = next.data?.item ?? null
  // «Двойник в буфере» (spec-antiassist §2.1): копирование условия кладёт в буфер текст с изменённым токеном, вставка в своё
  // поле возвращает исходный; события copy/cut/dragstart/self_paste шлёт сам хук (useTestEvents их не дублирует).
  useClipboardTwin(item, { sessionId })

  // Дедлайн: «сколько секунд осталось» сервер сообщает на момент ответа — от него и отсчитываем
  const deadlineMs = useMemo(
    () => (item ? Date.now() + item.seconds_left * 1000 : 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- пересчитываем только при новом задании или новом ответе сервера
    [item?.seq, next.dataUpdatedAt],
  )
  useCountdown(deadlineMs) // раз в секунду перерисовывает экран; остаток считаем от дедлайна сами
  const remaining = item ? Math.max(0, Math.ceil((deadlineMs - Date.now()) / 1000)) : 0

  const answer = useMutation({
    mutationFn: (v: { seq: number; answer: AnswerValue }) =>
      unwrap(
        api.POST('/api/tests/sessions/{session_id}/answer', {
          params: { path: { session_id: sessionId } },
          body: { seq: v.seq, answer: v.answer },
        }),
      ),
  })

  async function submit(value: AnswerValue, timedOut = false) {
    if (!item || answer.isPending || advancing) return
    setSubmitError(null)
    try {
      const res = await answer.mutateAsync({ seq: item.seq, answer: value })
      if (timedOut || res.timed_out) toast.show({ message: 'Время на это задание вышло — переходим к следующему.' })
      await flush()
      if (res.finished) {
        void qc.invalidateQueries({ queryKey: qk.qualifications })
        navigate(`/candidate/result/${sessionId}`, { replace: true })
        return
      }
      setAdvancing(true)
      await next.refetch()
    } catch (e) {
      if (e instanceof ApiException && e.code === 'no_pending_item') {
        setAdvancing(true)
        await next.refetch()
      } else if (e instanceof ApiException && e.code === 'session_not_in_progress') {
        navigate(`/candidate/result/${sessionId}`, { replace: true })
      } else {
        setSubmitError(errorMessage(e))
      }
    } finally {
      setAdvancing(false)
    }
  }

  // таймер истёк: отправляем то, что успели ввести (сервер сам решит, засчитывать ли)
  useEffect(() => {
    if (!item || deadlineMs === 0 || Date.now() < deadlineMs) return
    if (autoSent.current === item.seq || answer.isPending || advancing) return
    autoSent.current = item.seq
    void submit(answerRef.current(), true)
  })

  // --- состояния экрана ---
  if (next.isPending) {
    return (
      <>
        <MobileHeader title="Тест" />
        <div className="flex flex-col gap-4" role="status" aria-label="Загружаем задание">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-36 w-full rounded-panel-lg" />
          <Skeleton className="h-12 w-full rounded-cell" />
        </div>
      </>
    )
  }

  if (next.isError && !next.data) {
    const notFound = next.error instanceof ApiException && next.error.status === 404
    if (notFound) {
      return (
        <>
          <MobileHeader title="Тест" backTo="/candidate" />
          <EmptyState
            headingLevel={1}
            title="Тест не найден"
            description="Возможно, ссылка устарела. Начните с главного экрана."
            action={<Button to="/candidate">На главный экран</Button>}
          />
        </>
      )
    }
    return (
      <>
        <MobileHeader title="Тест" backTo="/candidate" />
        <Panel variant="tint" radius="lg" padding="md" className="flex flex-col gap-3">
          <p className="t-name text-ink">Тест на паузе</p>
          <p className="t-body text-ink-2">
            {online ? errorMessage(next.error) : 'Нет связи с сервером.'} Ваши ответы сохранены. Вернуться можно в течение 30 минут:
            время на текущее задание продолжает идти на сервере.
          </p>
          <div>
            <Button onClick={() => void next.refetch()} loading={next.isFetching}>
              Продолжить
            </Button>
          </div>
        </Panel>
      </>
    )
  }

  if (!item) return <Navigate to={`/candidate/result/${sessionId}`} replace />

  const session = next.data!.session
  const position = session.progress.position
  const shownLeft = Math.min(remaining, item.time_limit_s)
  const express = session.express
  const expressMax = session.express_items_max ?? 8

  return (
    <>
      <MobileHeader
        title={`Тест · ${session.specialization.label}`}
        onBack={() => setExitOpen(true)}
        className="mb-4"
        right={
          <span
            role="timer"
            aria-label={`Осталось времени на задание: ${formatClock(shownLeft)}`}
            className={cn('font-mono text-[18px] font-medium leading-6 tabular-nums', shownLeft <= 15 ? 'text-danger-text' : 'text-ink')}
          >
            {formatClock(shownLeft)}
          </span>
        }
      />

      {express && (
        <ExpressStrip deadlineAt={session.express_deadline_at} />
      )}

      {!online && (
        <Alert tone="danger" className="mb-4" title="Нет связи">
          Тест на паузе. Когда связь вернётся, он продолжится с этого же задания — в течение 30 минут.
        </Alert>
      )}

      <div className="mb-5">
        <div className="mb-2 flex items-baseline justify-between gap-3">
          <p className="t-small text-ink-2">
            {express
              ? `Экспресс · задание ${position} из ${expressMax}`
              : position <= 15
                ? `Задание ${position} из примерно 12–15`
                : `Задание ${position} · осталось немного`}
          </p>
          <p className="font-mono text-[12px] text-ink-3" aria-label={`Код варианта ${item.variant_code}`}>
            вариант {item.variant_code}
          </p>
        </div>
        <TraceProgress done={Math.max(0, position - 1)} estimatedTotal={express ? expressMax : 15} label={`Пройдено заданий: ${Math.max(0, position - 1)}`} />
      </div>

      {advancing ? (
        <div className="flex flex-col gap-4" role="status" aria-label="Подбираем следующее задание">
          <p className="t-body text-ink-2">Подбираем следующее задание…</p>
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-36 w-full rounded-panel-lg" />
        </div>
      ) : (
        <ItemPlayer
          key={item.seq}
          item={item}
          submitting={answer.isPending}
          answerRef={answerRef}
          disabled={!online}
          hintAnswer={hint?.seq === item.seq ? hint.answer : undefined}
          onSubmit={(v) => void submit(v)}
        />
      )}

      {demo.data?.jury_mode && (
        <Panel variant="tint" radius="md" padding="sm" className="mt-6 flex items-center justify-between gap-3">
          <p className="min-w-0 t-small text-ink-2">Режим жюри: подсказка подставит верный ответ, сессия не попадёт в метрики.</p>
          <Button variant="outline" size="sm" loading={askHint.isPending} onClick={() => askHint.mutate()}>
            Подсказка
          </Button>
        </Panel>
      )}

      {submitError && (
        <Alert tone="danger" className="mt-4" title="Ответ не отправился">
          {submitError} Повторите — ответ запишется один раз.
        </Alert>
      )}

      <Panel variant="panel" radius="md" padding="sm" className="mt-8 flex flex-col gap-3 t-small text-ink-2">
        <p>Вернуться к прошлому заданию нельзя: тест подстраивает следующее задание под ваши ответы.</p>
        <p>У каждого кандидата свой вариант. Уход со вкладки и вставка текста фиксируются, камера не используется.</p>
      </Panel>

      <Sheet
        open={exitOpen}
        onClose={() => setExitOpen(false)}
        title="Выйти из теста?"
        description="Тест встанет на паузу. Вернуться можно в течение 30 минут, но таймер текущего задания продолжит идти на сервере."
      >
        <div className="flex flex-col gap-3">
          <Button block onClick={() => setExitOpen(false)}>
            Остаться
          </Button>
          <Button block variant="outline" onClick={() => navigate('/candidate')}>
            Выйти на главный экран
          </Button>
        </div>
      </Sheet>
    </>
  )
}

/** Плашка экспресса: честно говорит, что это сокращённая версия, и показывает общий бюджет времени. */
function ExpressStrip({ deadlineAt }: { deadlineAt?: string | null }) {
  const target = useMemo(() => (deadlineAt ? new Date(deadlineAt).getTime() : 0), [deadlineAt])
  const left = useCountdown(target)
  return (
    <Panel variant="tint" radius="md" padding="sm" className="mb-4 flex items-center justify-between gap-3" data-testid="express-strip">
      <p className="min-w-0 t-small text-ink-2">
        <span className="t-label text-ink">Экспресс-версия для жюри.</span> Не больше 8 заданий; категорию не присваивает.
      </p>
      {target > 0 && (
        <span className="shrink-0 font-mono text-[13px] text-ink-2 tabular-nums" aria-label="Осталось времени на весь экспресс">
          {formatClock(left)}
        </span>
      )}
    </Panel>
  )
}
