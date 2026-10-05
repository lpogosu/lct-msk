import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import { Chip, SectionTitle, TraceList } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { qk, useSurveyState, type Briefing } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { formatDate } from '@/lib/format'
import { Alert, Button, Checkbox, EmptyState, MobileHeader, Panel, Ruler } from '@/ui'

const TESTABLE = ['backend_python', 'frontend_js']

/**
 * К3 «Грейд и брифинг»: карточки грейдов с поведенческими якорями, подсказка по стажу, правила теста,
 * отдельная галочка согласия на журналирование событий и «Начать». Для подтверждённого грейда — режим «на повышение».
 */
export default function CandidateBriefing() {
  const [params, setParams] = useSearchParams()
  const survey = useSurveyState()
  const explicit = params.get('spec')

  return (
    <>
      <MobileHeader title="Грейд и правила" backTo="/candidate" />
      <QueryBoundary query={survey}>
        {(s) => {
          const spec = explicit ?? (s.answers.chosen_spec && TESTABLE.includes(s.answers.chosen_spec) ? s.answers.chosen_spec : null)
          if (!spec) return <SpecChooser surveyDone={s.completed} chosen={s.answers.chosen_spec ?? null} onPick={(c) => setParams({ spec: c })} />
          return <BriefingFor spec={spec} />
        }}
      </QueryBoundary>
    </>
  )
}

function SpecChooser({ surveyDone, chosen, onPick }: { surveyDone: boolean; chosen: string | null; onPick: (c: string) => void }) {
  if (!surveyDone) {
    return (
      <EmptyState
        headingLevel={1}
        title="Сначала короткий опрос"
        description="Он нужен, чтобы подсказать направление и грейд. Занимает около 3 минут."
        action={<Button to="/candidate/survey">Пройти опрос</Button>}
      />
    )
  }
  const waitlist = chosen != null && !TESTABLE.includes(chosen) && chosen !== 'fullstack'
  if (waitlist) {
    return (
      <EmptyState
        headingLevel={1}
        image="empty_state"
        title="Тест по вашему направлению скоро"
        description="Вы в листе ожидания: мы сообщим, когда он появится. Пока можно привязать ФСП ID и заполнить профиль — отсутствие истории ФСП не штраф."
        action={
          <Button to="/candidate/profile" variant="outline">
            Профиль
          </Button>
        }
      />
    )
  }
  return (
    <div className="flex flex-col gap-5">
      <h1 className="t-title text-brand">Что проверим?</h1>
      <p className="t-body text-ink-2">Категория Fullstack появится, когда подтверждены обе составляющие. Начните с любой.</p>
      <Button block size="lg" onClick={() => onPick('backend_python')}>
        Backend (Python)
      </Button>
      <Button block variant="outline" onClick={() => onPick('frontend_js')}>
        Frontend (JS/TS)
      </Button>
    </div>
  )
}

function BriefingFor({ spec }: { spec: string }) {
  const q = useQuery({
    queryKey: qk.briefing(spec),
    queryFn: () => unwrap(api.GET('/api/tests/briefing', { params: { query: { specialization: spec } } })),
    retry: false,
  })
  return <QueryBoundary query={q}>{(b) => <BriefingView b={b} />}</QueryBoundary>
}

function BriefingView({ b }: { b: Briefing }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const upgrade = b.mode === 'upgrade'
  const [grade, setGrade] = useState<string | null>(upgrade ? (b.upgrade_target?.code ?? null) : null)
  const [consent, setConsent] = useState(false)
  const [error, setError] = useState<{ message: string; availableAt?: string } | null>(null)

  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/tests/sessions', {
          body: {
            specialization: b.specialization.code,
            grade: grade,
            upgrade,
            consent_test_logging: b.consent.given || consent,
          },
        }),
      ),
    onSuccess: (s) => {
      void qc.invalidateQueries({ queryKey: qk.qualifications })
      void qc.invalidateQueries({ queryKey: qk.briefing(b.specialization.code) })
      navigate(`/candidate/test/${s.id}`)
    },
    onError: (e) => {
      const details = e instanceof ApiException && e.details && typeof e.details === 'object' ? (e.details as { available_at?: string }) : null
      setError({ message: errorMessage(e), availableAt: details?.available_at })
    },
  })

  if (!b.testable) {
    return (
      <EmptyState
        headingLevel={1}
        title="Теста по этому направлению пока нет"
        description="Вы в листе ожидания. Пока можно привязать ФСП ID и заполнить профиль."
        action={<Button to="/candidate/profile">Профиль</Button>}
      />
    )
  }
  if (!b.survey_done) {
    return (
      <EmptyState
        headingLevel={1}
        title="Сначала короткий опрос"
        description="Он нужен, чтобы подсказать грейд. Занимает около 3 минут."
        action={<Button to="/candidate/survey">Пройти опрос</Button>}
      />
    )
  }

  const selected = b.grades.find((g) => g.code === grade) ?? null
  const consentOk = b.consent.given || consent
  const canStart = b.can_start.ok && !!grade && consentOk

  return (
    <div className="flex flex-col gap-7">
      <div>
        <p className="mb-1 t-body text-ink-3">{b.specialization.label}</p>
        <h1 className="t-title text-brand">{upgrade ? 'Тест на повышение' : 'Какой грейд проверим?'}</h1>
        {b.experience_hint && !upgrade && <p className="mt-2 t-body text-ink-2">{b.experience_hint}</p>}
        {upgrade && b.current_grade && b.upgrade_target && (
          <p className="mt-2 t-body text-ink-2">
            Сейчас у вас подтверждён {b.current_grade.name}. Проверим {b.upgrade_target.name}: ваш нынешний грейд при этом не снижается.
          </p>
        )}
      </div>

      {b.active_session_id && (
        <Panel variant="tint" radius="lg" padding="sm" className="flex flex-col gap-3">
          <p className="t-body-strong text-ink">У вас есть незавершённый тест</p>
          <p className="t-small text-ink-2">Вернуться можно в течение 30 минут после последнего ответа.</p>
          <div>
            <Button size="sm" to={`/candidate/test/${b.active_session_id}`}>
              Продолжить тест
            </Button>
          </div>
        </Panel>
      )}

      <Ruler size="full" value={selected?.score} />

      <section aria-labelledby="grades-h">
        <SectionTitle>
          <span id="grades-h">{upgrade ? 'Грейд для проверки' : 'Грейд'}</span>
        </SectionTitle>
        <div role="radiogroup" aria-labelledby="grades-h" className="flex flex-col gap-2.5">
          {b.grades
            .filter((g) => !upgrade || g.code === b.upgrade_target?.code)
            .map((g) => {
              const on = grade === g.code
              return (
                <button
                  key={g.code}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() => setGrade(g.code)}
                  className={cn(
                    'flex flex-col gap-1 rounded-panel-lg border px-4 py-3.5 text-left transition-colors',
                    on ? 'border-brand bg-tint' : 'border-hairline bg-white hover:border-steel',
                  )}
                >
                  <span className="flex items-center justify-between gap-3">
                    <span className="t-name text-ink">{g.name}</span>
                    <span className="flex items-center gap-2">
                      {g.suggested && <Chip tone="tint">по стажу ближе</Chip>}
                      <span className="font-mono text-[13px] text-ink-3">от {g.score}</span>
                    </span>
                  </span>
                  {g.anchor && <span className="t-small text-ink-2">{g.anchor}</span>}
                </button>
              )
            })}
        </div>
        {selected?.code === 'senior' && b.senior_warning && (
          <Alert className="mt-3">{b.senior_warning}</Alert>
        )}
        <p className="mt-3 t-small text-ink-3">Выбор ничего не гарантирует: уровень определит тест, а не заявка.</p>
      </section>

      <section aria-labelledby="rules-h">
        <SectionTitle>
          <span id="rules-h">Правила теста</span>
        </SectionTitle>
        <TraceList items={b.rules} />
      </section>

      {!b.consent.given ? (
        <Panel variant="panel" radius="md" padding="sm">
          <Checkbox
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            label={<span className="t-body-strong">{b.consent.title}</span>}
            description={b.consent.summary}
          />
        </Panel>
      ) : (
        <p className="t-small text-ink-3">Согласие на журналирование событий теста дано (версия {b.consent.version}). Отозвать можно в журнале согласий.</p>
      )}

      {!b.can_start.ok && (
        <Alert title="Начать сейчас нельзя">
          {b.can_start.message}
          {b.can_start.available_at ? `. Доступно с ${formatDate(b.can_start.available_at)}.` : ''}
        </Alert>
      )}
      {error && (
        <Alert tone="danger">
          {error.message}
        </Alert>
      )}

      <div className="flex flex-col gap-2">
        <Button block size="lg" disabled={!canStart} loading={start.isPending} onClick={() => start.mutate()}>
          {upgrade ? 'Начать тест на повышение' : 'Начать тест'}
        </Button>
        {!canStart && b.can_start.ok && (
          <p className="text-center t-small text-ink-3">
            {!grade ? 'Выберите грейд.' : 'Отметьте согласие на журналирование событий теста.'}
          </p>
        )}
      </div>
    </div>
  )
}
