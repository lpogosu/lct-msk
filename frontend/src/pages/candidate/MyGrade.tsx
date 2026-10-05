import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { Chip, SectionTitle, SkillChip } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { Sheet } from '@/features/candidate/components/Sheet'
import { qk, useQualifications, type Qualification } from '@/features/candidate/queries'
import { Alert, Button, EmptyState, MobileHeader, Panel, Ruler, TextArea, useToast } from '@/ui'

type Flag = NonNullable<Qualification['flags']>[number]

/**
 * К6 «Мой грейд»: категория, балл с интервалом, уверенность, актуальность, модули (подтверждено тестом / заявлено),
 * даты следующего теста и смены грейда, история смен и попыток, лента опросов, свои флаги с «оспорить».
 * Работодатель этого экрана не видит.
 */
export default function CandidateMyGrade() {
  const q = useQualifications()
  return (
    <>
      <MobileHeader title="Мой грейд" backTo="/candidate/profile" />
      <QueryBoundary query={q}>
        {(list) => (
          <div className="flex flex-col gap-10">
            <h1 className="t-title text-brand">Мой грейд</h1>
            {list.length === 0 ? (
              <EmptyState
                headingLevel={2}
                title="Грейда пока нет"
                description="Он появится после опроса и адаптивного теста. Резюме и самооценка на него не влияют."
                action={<Button to="/candidate/survey">Пройти опрос</Button>}
              />
            ) : (
              list.map((qual) => <QualificationCard key={qual.specialization.code} qual={qual} />)
            )}
            <SurveyHistory />
          </div>
        )}
      </QueryBoundary>
    </>
  )
}

function QualificationCard({ qual }: { qual: Qualification }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [confirmDown, setConfirmDown] = useState(false)
  const [dispute, setDispute] = useState<Flag | null>(null)
  const interval = qual.interval80 && qual.interval80.length === 2 ? ([qual.interval80[0], qual.interval80[1]] as const) : undefined
  const confirmed = qual.status === 'CONFIRMED' || qual.status === 'UPGRADE_TESTING'
  const spec = qual.specialization.code
  const history = qual.history ?? []
  const attempts = qual.attempts ?? []
  const flags = qual.flags ?? []
  const confirmedModules = qual.modules_confirmed ?? []
  const declared = qual.modules_declared ?? []

  const downgrade = useMutation({
    mutationFn: () => unwrap(api.POST('/api/tests/qualification/{specialization}/downgrade', { params: { path: { specialization: spec } } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.qualifications })
      toast.success('Грейд понижен на одну ступень по вашему желанию.')
      setConfirmDown(false)
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  const activeAttempt = attempts.find((a) => ['CREATED', 'IN_PROGRESS', 'PAUSED'].includes(a.status))

  return (
    <article className="flex flex-col gap-6" aria-label={qual.specialization.label}>
      <div>
        <p className="t-body text-ink-3">{qual.specialization.label}</p>
        <h2 className="mt-1 font-mono text-[28px] font-medium leading-[34px] tracking-[-0.5px] text-brand">
          {qual.category?.label ?? qual.status_ru}
        </h2>
        <p className="mt-1 t-small text-ink-2">
          {qual.status_ru}
          {qual.borderline ? ' · на границе грейда' : ''}
        </p>
      </div>

      {qual.hidden && qual.hidden_message && (
        <Alert title="Результат на проверке">{qual.hidden_message}</Alert>
      )}

      {qual.score != null && (
        <div className="flex flex-col gap-4">
          <Ruler size="full" value={qual.score} interval={interval} />
          <dl className="flex flex-wrap gap-x-8 gap-y-3">
            <div>
              <dt className="t-small text-ink-3">Балл</dt>
              <dd className="t-num-m text-ink">{qual.score}</dd>
            </div>
            {interval && (
              <div>
                <dt className="t-small text-ink-3">Интервал</dt>
                <dd className="t-num-m text-ink">
                  {interval[0]}–{interval[1]}
                </dd>
              </div>
            )}
            {qual.confidence_pct != null && (
              <div>
                <dt className="t-small text-ink-3">Уверенность</dt>
                <dd className="t-num-m text-ink">{qual.confidence_pct}%</dd>
              </div>
            )}
            {qual.freshness && (
              <div>
                <dt className="t-small text-ink-3">Актуальность</dt>
                <dd className="t-label text-ink">
                  {qual.freshness.band_ru}
                  <span className="block t-small font-normal text-ink-3">{qual.freshness.since}</span>
                </dd>
              </div>
            )}
            {qual.trust && (
              <div>
                <dt className="t-small text-ink-3">Доверие</dt>
                <dd className="t-label text-ink">{qual.trust}</dd>
              </div>
            )}
          </dl>
        </div>
      )}

      {(confirmedModules.length > 0 || declared.length > 0) && (
        <section>
          <SectionTitle as="h3">Модули</SectionTitle>
          {confirmedModules.length > 0 && (
            <ul className="mb-3 flex flex-wrap gap-2" aria-label="Подтверждено тестом">
              {confirmedModules.map((m) => (
                <li key={m.module}>
                  <SkillChip name={`${m.name}${m.symbol ? ' ' + m.symbol : ''}`} kind={m.tag === 'strong' ? 'fsp' : 'test'} />
                </li>
              ))}
            </ul>
          )}
          {declared.length > 0 && (
            <>
              <p className="mb-2 t-small text-ink-3">Заявлено вами в опросе — серым, в оценку не входит</p>
              <ul className="flex flex-wrap gap-2">
                {declared.map((m) => (
                  <li key={m.code}>
                    <SkillChip name={m.label} kind="claimed" />
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <section className="flex flex-col gap-3">
        <SectionTitle as="h3">Следующий тест</SectionTitle>
        {activeAttempt ? (
          <Panel variant="tint" radius="lg" padding="sm" className="flex flex-col gap-2">
            <p className="t-body-strong text-ink">Тест не завершён</p>
            <div>
              <Button size="sm" to={`/candidate/test/${activeAttempt.session_id}`}>
                Продолжить тест
              </Button>
            </div>
          </Panel>
        ) : (
          <>
            {!confirmed && (
              <RuleRow
                rule={qual.can_start}
                action={
                  qual.can_start.ok ? (
                    <Button size="sm" to={`/candidate/grade?spec=${spec}`}>
                      {qual.status === 'NOT_CONFIRMED' ? 'Пройти тест ещё раз' : 'Выбрать грейд и начать'}
                    </Button>
                  ) : (
                    <Button size="sm" disabled>
                      Пока недоступно
                    </Button>
                  )
                }
              />
            )}
            {confirmed && (
              <RuleRow
                rule={qual.can_upgrade}
                action={
                  qual.can_upgrade.ok ? (
                    <Button size="sm" to={`/candidate/grade?spec=${spec}`}>
                      Тест на повышение
                    </Button>
                  ) : (
                    <Button size="sm" disabled>
                      Тест на повышение
                    </Button>
                  )
                }
              />
            )}
          </>
        )}
        {qual.next_change_in && <p className="t-small text-ink-3">Следующая смена грейда — {qual.next_change_in}.</p>}
        {qual.next_attempt_in && <p className="t-small text-ink-3">Следующая попытка — {qual.next_attempt_in}.</p>}
        {confirmed && qual.grade && qual.grade.code !== 'intern' && (
          <div>
            <Button variant="text" onClick={() => setConfirmDown(true)}>
              Перейти на грейд ниже
            </Button>
          </div>
        )}
      </section>

      {flags.length > 0 && (
        <section>
          <SectionTitle as="h3">Замечания к тесту</SectionTitle>
          <p className="mb-3 t-small text-ink-3">Это признаки, на которые система обратила внимание. Не согласны — оспорьте: флаг уйдёт методисту первым в очереди.</p>
          <ul className="flex flex-col gap-3">
            {flags.map((f) => (
              <li key={f.id}>
                <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <Chip tone={f.severity === 'hard' ? 'danger' : 'panel'}>{f.status_ru}</Chip>
                    <span className="t-small text-ink-3">{f.created_ago}</span>
                  </div>
                  <p className="t-body text-ink">{f.message}</p>
                  {f.dispute_text && <p className="t-small text-ink-2">Ваше возражение: «{f.dispute_text}»</p>}
                  {f.can_dispute && (
                    <div>
                      <Button variant="outline" size="sm" onClick={() => setDispute(f)}>
                        Оспорить
                      </Button>
                    </div>
                  )}
                </Panel>
              </li>
            ))}
          </ul>
        </section>
      )}

      {history.length > 0 && (
        <section>
          <SectionTitle as="h3">Смены грейда</SectionTitle>
          <ul className="flex flex-col gap-2">
            {history.map((h, i) => (
              <li key={i} className="flex items-baseline justify-between gap-3 border-b border-hairline pb-2 t-body text-ink">
                <span>
                  {h.from_grade ? `${h.from_grade.name} → ` : ''}
                  {h.to_grade?.name ?? '—'}
                  <span className="block t-small text-ink-3">{h.reason_ru}</span>
                </span>
                <span className="shrink-0 t-small text-ink-3">{h.ago}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {attempts.length > 0 && (
        <section>
          <SectionTitle as="h3">Попытки</SectionTitle>
          <ul className="flex flex-col gap-2">
            {attempts.map((a) => (
              <li key={a.session_id} className="flex items-baseline justify-between gap-3 border-b border-hairline pb-2">
                <span className="t-body text-ink">
                  {a.kind_ru} · {a.claimed_grade.name}
                  <span className="block t-small text-ink-3">
                    {a.decision_ru ?? a.status_ru}
                    {a.score != null ? ` · ${a.score}` : ''}
                  </span>
                </span>
                <span className="flex shrink-0 flex-col items-end gap-0.5 t-small text-ink-3">
                  {a.started_ago}
                  {a.decision && (
                    <Link to={`/candidate/result/${a.session_id}`} className="text-brand underline-offset-4 hover:underline">
                      Результат
                    </Link>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Sheet
        open={confirmDown}
        onClose={() => setConfirmDown(false)}
        title="Перейти на грейд ниже?"
        description="Добровольное понижение на одну ступень. Это считается сменой грейда: вне первых 24 часов после теста — не чаще раза в 90 дней."
      >
        <div className="flex flex-col gap-3">
          <Button block variant="outline" loading={downgrade.isPending} onClick={() => downgrade.mutate()}>
            Перейти на ступень ниже
          </Button>
          <Button block variant="text" onClick={() => setConfirmDown(false)}>
            Оставить как есть
          </Button>
        </div>
      </Sheet>

      <DisputeFlagSheet flag={dispute} onClose={() => setDispute(null)} />
    </article>
  )
}

function RuleRow({ rule, action }: { rule: Qualification['can_start']; action: React.ReactNode }) {
  return (
    <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-2">
      <p className="t-body text-ink">{rule.message}</p>
      {rule.available_in && !rule.ok && <p className="t-small text-ink-3">Станет доступно {rule.available_in}.</p>}
      <div>{action}</div>
    </Panel>
  )
}

function DisputeFlagSheet({ flag, onClose }: { flag: Flag | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [text, setText] = useState('')
  const send = useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/tests/flags/{flag_id}/dispute', { params: { path: { flag_id: flag!.id } }, body: { text: text.trim() } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.qualifications })
      toast.success('Возражение отправлено методисту.')
      setText('')
      onClose()
    },
  })
  return (
    <Sheet open={flag != null} onClose={onClose} title="Оспорить замечание" description={flag?.message}>
      <div className="flex flex-col gap-4">
        <TextArea
          label="Почему замечание ошибочно"
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, 1000))}
          rows={4}
          hint={`${text.length}/1000. Например: «вставлял из своего черновика»`}
        />
        {send.error ? <Alert tone="danger">{errorMessage(send.error)}</Alert> : null}
        <Button block size="lg" disabled={text.trim().length < 3} loading={send.isPending} onClick={() => send.mutate()}>
          Отправить возражение
        </Button>
      </div>
    </Sheet>
  )
}

function SurveyHistory() {
  const q = useQuery({ queryKey: qk.surveyHistory, queryFn: () => unwrap(api.GET('/api/survey/history')) })
  if (!q.data || q.data.length === 0) return null
  return (
    <section>
      <SectionTitle as="h3">История опросов</SectionTitle>
      <ul className="flex flex-col gap-2">
        {q.data.map((s) => (
          <li key={s.survey_id} className="flex items-baseline justify-between gap-3 border-b border-hairline pb-2">
            <span className="t-body text-ink">
              Опрос № {s.number}: {s.chosen_name ?? '—'}
              {s.top && s.top !== s.chosen_spec && <span className="block t-small text-ink-3">По ответам ближе: {s.top_name}</span>}
            </span>
            <span className="shrink-0 t-small text-ink-3">{s.completed_ago}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3">
        <Button variant="text" to="/candidate/survey">
          Пройти опрос заново
        </Button>
      </div>
    </section>
  )
}
