import { Link } from 'react-router-dom'
import { NotificationsBell } from '@/features/candidate/components/Notifications'
import { ErrorBlock } from '@/features/candidate/components/QueryBoundary'
import { Chip } from '@/features/candidate/components/Bits'
import { Journey, type JourneyStep } from '@/features/candidate/components/Journey'
import { salaryLine } from '@/features/candidate/format'
import {
  mainQualification,
  openAttempt,
  useFsp,
  useInbox,
  usePrivacy,
  useProfile,
  useQualifications,
  useSurveyState,
  useTasks,
  type FspStatus,
  type ProfileOut,
  type Qualification,
  type SurveyState,
} from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { plural } from '@/lib/format'
import { Button, Panel, Ruler, Skeleton } from '@/ui'

/**
 * К1 «Мой путь»: трасса пути (опрос → грейд → тест → категория → ФСП → профиль), карточка категории,
 * новые приглашения, предложенная задача. Каждый блок грузится сам: сбой одного не ломает экран.
 */
export default function CandidateHome() {
  const survey = useSurveyState()
  const quals = useQualifications()
  const inbox = useInbox()
  const tasks = useTasks()
  const fsp = useFsp()
  const profile = useProfile()
  const privacy = usePrivacy()

  const newCount = inbox.data?.new_count ?? 0
  const category = quals.data ? mainQualification(quals.data) : null
  const confirmed = category?.status === 'CONFIRMED' || category?.status === 'UPGRADE_TESTING' ? category : null

  return (
    <div className="flex flex-col gap-8">
      <header>
        <div className="-mr-2 flex items-center justify-between gap-3">
          <h1 className="t-title text-brand">Мой путь</h1>
          <NotificationsBell />
        </div>
        <p className="mt-2 t-body text-ink-2">
          {confirmed
            ? 'Категория подтверждена. Теперь работодатели могут найти вас по уровню, а не по резюме.'
            : 'Четыре шага до категории «специализация × грейд». Резюме на неё не влияет — только тест.'}
        </p>
      </header>

      {inbox.data && inbox.data.items.length > 0 && <InvitationsCard inbox={inbox.data} newCount={newCount} />}

      {quals.isPending || survey.isPending ? (
        <Skeleton className="h-40 w-full rounded-hero" />
      ) : quals.isError ? (
        <ErrorBlock error={quals.error} onRetry={() => void quals.refetch()} compact />
      ) : (
        <CategoryCard
          qual={confirmed}
          others={(quals.data ?? []).filter(
            (x) => x !== confirmed && (x.status === 'CONFIRMED' || x.status === 'UPGRADE_TESTING') && x.category,
          )}
          next={nextAction(survey.data, quals.data ?? [])}
          waitlistName={waitlistName(survey.data)}
        />
      )}

      {survey.isError ? (
        <ErrorBlock error={survey.error} onRetry={() => void survey.refetch()} compact />
      ) : (
        <section aria-labelledby="journey-h">
          <h2 id="journey-h" className="mb-4 t-name text-ink">
            Шаги
          </h2>
          {survey.isPending || quals.isPending ? (
            <div className="flex flex-col gap-4">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : (
            <Journey
              steps={buildJourney({
                survey: survey.data,
                quals: quals.data ?? [],
                fsp: fsp.data,
                profile: profile.data,
              })}
            />
          )}
        </section>
      )}

      {tasks.data && tasks.data.active.length > 0 && (
        <section aria-labelledby="task-h">
          <h2 id="task-h" className="mb-3 t-name text-ink">
            Вам предложили задачу
          </h2>
          {tasks.data.active.slice(0, 1).map((t) => (
            <Panel key={t.id} variant="tint" radius="lg" padding="sm" className="flex flex-col gap-2">
              <p className="t-body-strong text-ink">{t.title}</p>
              <p className="t-small text-ink-2">
                {t.origin.label} · {t.minutes} мин{t.due_text ? ` · ${t.due_text}` : ''}
              </p>
              <p className="t-small text-ink-3">{t.effect}</p>
              <div className="mt-1">
                <Button to="/candidate/tasks" size="sm">
                  Посмотреть задачи
                </Button>
              </div>
            </Panel>
          ))}
        </section>
      )}

      {privacy.data && (
        <Panel variant="panel" radius="md" padding="sm" className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className={cn('mt-[7px] h-2 w-2 shrink-0 rounded-full', privacy.data.bank.visible ? 'bg-brand' : 'bg-steel')}
          />
          <div className="min-w-0 t-small text-ink-2">
            {privacy.data.bank.visible ? (
              <p>
                Вас видят работодатели: слепая карточка без имени и контактов.{' '}
                <Link to="/candidate/privacy" className="text-brand underline-offset-4 hover:underline">
                  Как меня видят
                </Link>
              </p>
            ) : (
              <>
                <p className="t-label text-ink">Работодатели вас пока не видят</p>
                <p className="mt-0.5">{(privacy.data.bank.reasons ?? [])[0] ?? 'Проверьте настройки приватности.'}</p>
                <Link to="/candidate/privacy" className="mt-1 inline-block text-brand underline-offset-4 hover:underline">
                  Приватность и поиск
                </Link>
              </>
            )}
          </div>
        </Panel>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------------------

function InvitationsCard({
  inbox,
  newCount,
}: {
  inbox: NonNullable<ReturnType<typeof useInbox>['data']>
  newCount: number
}) {
  const shown = inbox.items.filter((i) => ['sent', 'viewed', 'countered', 'accepted'].includes(i.status)).slice(0, 2)
  return (
    <section aria-labelledby="inv-h">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 id="inv-h" className="t-name text-ink">
          Приглашения
        </h2>
        {newCount > 0 && <Chip tone="brand">{newCount} {plural(newCount, ['новое', 'новых', 'новых'])}</Chip>}
      </div>
      <ul className="flex flex-col gap-3">
        {shown.map((inv) => (
          <li key={inv.id}>
            <Link
              to={`/candidate/invitations/${inv.id}`}
              className="block rounded-panel-lg border border-hairline p-4 no-underline hover:bg-panel"
            >
              <p className="font-mono text-[20px] font-medium leading-6 tracking-tight text-brand">
                {salaryLine(inv.salary.salary_from, inv.salary.salary_to)}
              </p>
              <p className="mt-1 t-label text-ink">{inv.company?.name ?? 'Компания'}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 t-small text-ink-3">
                <span>{inv.status_label}</span>
                {inv.timers.respond_by_text && <span className="text-danger-text">· ответить {inv.timers.respond_by_text}</span>}
              </p>
            </Link>
          </li>
        ))}
      </ul>
      <div className="mt-3">
        <Link to="/candidate/invitations" className="t-label text-brand underline-offset-4 hover:underline">
          Все приглашения
        </Link>
      </div>
    </section>
  )
}

function CategoryCard({
  qual,
  others = [],
  next,
  waitlistName,
}: {
  qual: Qualification | null
  /** Остальные подтверждённые категории: по каждой специализации — своя (Backend и QA одновременно) */
  others?: Qualification[]
  next: { label: string; to: string }
  waitlistName: string | null
}) {
  if (qual && qual.category) {
    return (
      <Panel variant="hero" radius="hero" padding="md" className="flex flex-col gap-4">
        <div>
          <p className="t-small text-ink-3">Ваша категория</p>
          <p className="mt-1 font-mono text-[24px] font-medium leading-[30px] tracking-[-0.4px] text-brand">{qual.category.label}</p>
        </div>
        {qual.score != null && (
          <Ruler
            value={qual.score}
            interval={(qual.interval80 ?? []).length === 2 ? ([qual.interval80![0], qual.interval80![1]] as const) : undefined}
            size="full"
          />
        )}
        <dl className="flex flex-wrap gap-x-8 gap-y-2">
          {qual.score != null && (
            <div>
              <dt className="t-small text-ink-3">Балл</dt>
              <dd className="t-num-m text-ink">{qual.score}</dd>
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
              <dd className="t-label text-ink">{qual.freshness.band_ru}</dd>
            </div>
          )}
        </dl>
        {qual.hidden && qual.hidden_message && <p className="t-small text-ink-2">{qual.hidden_message}</p>}
        {others.length > 0 && (
          <p className="t-small text-ink-2">
            Также подтверждено: {others.map((o) => o.category?.label).filter(Boolean).join(' · ')}
          </p>
        )}
        <Link to="/candidate/qualification" className="t-label text-brand underline-offset-4 hover:underline">
          Мой грейд: модули, история, следующий тест
        </Link>
      </Panel>
    )
  }
  if (waitlistName) {
    return (
      <Panel variant="tint" radius="hero" padding="md" className="flex flex-col gap-3">
        <p className="t-name text-ink">Вы в листе ожидания: «{waitlistName}»</p>
        <p className="t-body text-ink-2">
          Тест по этому направлению ещё готовится. Пока можно привязать ФСП ID и заполнить профиль — отсутствие истории ФСП не штраф.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button to="/candidate/fsp" size="sm">
            ФСП ID
          </Button>
          <Button to="/candidate/profile/edit" size="sm" variant="outline">
            Профиль и резюме
          </Button>
        </div>
      </Panel>
    )
  }
  return (
    <Panel variant="tint" radius="hero" padding="md" className="flex flex-col gap-3">
      <p className="t-name text-ink">Пройдите тест, чтобы работодатели вас увидели</p>
      <p className="t-body text-ink-2">
        Вас находят по категории «специализация × грейд». Она появится после адаптивного теста: обычно 12–15 заданий и около 20 минут.
      </p>
      <div>
        <Button to={next.to} size="md">
          {next.label}
        </Button>
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------------------------------------------------------

/** «Рабочая» квалификация: идущий тест → подтверждённая → выбранная в опросе → любая. */
function pickActive(survey: SurveyState | undefined, quals: Qualification[]): Qualification | null {
  const chosen = survey?.answers.chosen_spec ?? null
  return (
    quals.find((q) => q.status === 'TESTING' || q.status === 'UPGRADE_TESTING') ??
    mainQualification(quals.filter((q) => q.status === 'CONFIRMED')) ??
    quals.find((q) => q.specialization.code === chosen) ??
    quals.find((q) => q.status !== 'NONE') ??
    null
  )
}

/** Тест решён, но есть предложение добора: результат уже можно смотреть. */
function pendingResult(q: Qualification | null): string | null {
  return (q?.attempts ?? []).find((a) => a.status === 'EXTENSION_OFFERED')?.session_id ?? null
}

/** Направление без теста (лист ожидания): название или null. */
function waitlistName(survey: SurveyState | undefined): string | null {
  const c = survey?.classification
  return survey?.completed && c?.chosen_status === 'survey_only' ? c.chosen_name : null
}

/** Что делать дальше — одна главная кнопка на главном экране. */
function nextAction(survey: SurveyState | undefined, quals: Qualification[]): { label: string; to: string } {
  if (!survey?.completed) return { label: survey && survey.step > 0 ? 'Продолжить опрос' : 'Начать с опроса', to: '/candidate/survey' }
  const active = pickActive(survey, quals)
  const open = openAttempt(active)
  if (open) return { label: 'Продолжить тест', to: `/candidate/test/${open.sessionId}` }
  const result = pendingResult(active)
  if (result) return { label: 'Посмотреть результат', to: `/candidate/result/${result}` }
  const spec = active?.specialization.code ?? (survey.classification?.testable ? survey.answers.chosen_spec : null)
  return { label: 'Выбрать грейд', to: spec ? `/candidate/grade?spec=${spec}` : '/candidate/grade' }
}

function buildJourney({
  survey,
  quals,
  fsp,
  profile,
}: {
  survey: SurveyState | undefined
  quals: Qualification[]
  fsp: FspStatus | undefined
  profile: ProfileOut | undefined
}): JourneyStep[] {
  const cls = survey?.classification ?? null
  const surveyDone = !!survey?.completed
  const waitlist = surveyDone && cls != null && cls.chosen_status === 'survey_only'

  const active = pickActive(survey, quals)
  const attempt = openAttempt(active)
  const resultId = pendingResult(active)
  const status = active?.status ?? 'NONE'

  const gradeDone = ['TESTING', 'CONFIRMED', 'NOT_CONFIRMED', 'UPGRADE_TESTING'].includes(status)
  const testDone = ['CONFIRMED', 'NOT_CONFIRMED', 'UPGRADE_TESTING'].includes(status)
  const categoryDone = status === 'CONFIRMED' || status === 'UPGRADE_TESTING'

  const required = [
    {
      key: 'survey',
      title: 'Опрос',
      done: surveyDone,
      detail: surveyDone ? (
        <>
          Готово{cls ? ` · ${cls.chosen_name}` : ''}
        </>
      ) : survey && survey.step > 0 ? (
        `Сохранено шагов: ${survey.step} из 4 — продолжим с того же места`
      ) : (
        '4 коротких шага, около 3 минут. Сохраняем после каждого шага'
      ),
    },
    {
      key: 'grade',
      title: 'Грейд',
      done: gradeDone,
      detail: gradeDone
        ? active?.attempts?.[0]?.claimed_grade.name
          ? `Заявлен: ${active.attempts[0].claimed_grade.name}`
          : 'Выбран'
        : waitlist
          ? 'Тест по этому направлению скоро — вы в листе ожидания'
          : 'Выберите ступень, которую хотите подтвердить, и ознакомьтесь с правилами',
    },
    {
      key: 'test',
      title: 'Тест',
      done: testDone,
      detail:
        resultId && !attempt
          ? 'Ответы приняты — решите, нужен ли добор'
          : status === 'TESTING' || attempt
            ? 'Тест начат — можно продолжить'
          : testDone
            ? (active?.attempts?.[0]?.decision_ru ?? 'Пройден')
            : 'Адаптивный, около 12–15 заданий. У каждого кандидата свой вариант',
    },
    {
      key: 'category',
      title: 'Категория',
      done: categoryDone,
      detail: categoryDone ? (
        active?.category?.label
      ) : status === 'NOT_CONFIRMED' ? (
        <>
          Грейд пока не подтверждён.{' '}
          <Link to="/candidate/qualification" className="text-brand underline-offset-4 hover:underline">
            Что дальше
          </Link>
        </>
      ) : (
        'Появится после теста: специализация × грейд'
      ),
    },
  ]

  const firstPending = required.findIndex((r) => !r.done)
  const steps: JourneyStep[] = required.map((r, i) => ({
    key: r.key,
    title: r.title,
    detail: r.detail,
    state: r.done ? 'done' : i === firstPending && !waitlist ? 'current' : 'todo',
  }))

  const fspDone = !!fsp?.linked
  steps.push({
    key: 'fsp',
    title: 'ФСП',
    state: fspDone ? 'done' : 'optional',
    detail: fspDone ? (
      <>
        Привязан{fsp?.results?.length ? ` · результатов: ${fsp.results.length}` : ''}.{' '}
        <Link to="/candidate/fsp" className="text-brand underline-offset-4 hover:underline">
          Открыть
        </Link>
      </>
    ) : (
      <>
        По желанию, на грейд не влияет — отсутствие истории не штраф.{' '}
        <Link to="/candidate/fsp" className="text-brand underline-offset-4 hover:underline">
          Привязать ФСП ID
        </Link>
      </>
    ),
  })

  const profileDone = !!profile && (profile.missing ?? []).length === 0
  steps.push({
    key: 'profile',
    title: 'Профиль',
    state: profileDone ? 'done' : 'optional',
    detail: profileDone ? (
      'Заполнен'
    ) : (
      <>
        Резюме и контакты — «заявлено», на категорию не влияют.{' '}
        <Link to="/candidate/profile/edit" className="text-brand underline-offset-4 hover:underline">
          Заполнить
        </Link>
      </>
    ),
  })
  return steps
}
