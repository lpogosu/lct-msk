import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { Chip, ProbabilityBar, SkillChip } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { qk, useSurveyForm, useSurveyState, type SurveyForm, type SurveyState } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { Alert, Button, Input, MobileHeader, Panel, Segmented, Trace, fieldBorderClasses } from '@/ui'

type Freq = 'never' | 'sometimes' | 'regularly'

interface Draft {
  industries: string[]
  actions: Record<string, Freq>
  technologies: Record<string, string>
  years: string
  months: string
  roles: string[]
  selfAssessment: Record<string, number>
  chosenSpec: string | null
}

const STEP_TITLES = ['Сфера бизнеса', 'Что делали руками', 'Технологии и стаж', 'Направление'] as const

function draftFrom(state: SurveyState): Draft {
  const a = state.answers
  const total = a.experience_months
  return {
    industries: [...(a.industries ?? [])],
    actions: { ...((a.actions ?? {}) as Record<string, Freq>) },
    technologies: { ...(a.technologies ?? {}) },
    years: total != null ? String(Math.floor(total / 12)) : '',
    months: total != null ? String(total % 12) : '',
    roles: [...(a.roles ?? [])],
    selfAssessment: { ...(a.self_assessment ?? {}) },
    chosenSpec: a.chosen_spec ?? null,
  }
}

function experienceMonths(d: Draft): number {
  const y = Math.max(0, Math.min(50, Number.parseInt(d.years || '0', 10) || 0))
  const m = Math.max(0, Math.min(11, Number.parseInt(d.months || '0', 10) || 0))
  return Math.min(600, y * 12 + m)
}

/**
 * К2 «Опрос»: 4 шага, сохранение после каждого шага, продолжение с того же места, подсказка классификатора
 * на шаге специализации (решает кандидат) и сообщение листа ожидания для направлений без теста.
 */
export default function CandidateSurvey() {
  const state = useSurveyState()
  const form = useSurveyForm()
  return (
    <>
      <MobileHeader title="Опрос" backTo="/candidate" />
      <QueryBoundary query={state}>
        {(s) => (
          <QueryBoundary query={form}>{(f) => <SurveyFlow initial={s} form={f} />}</QueryBoundary>
        )}
      </QueryBoundary>
    </>
  )
}

function SurveyFlow({ initial, form }: { initial: SurveyState; form: SurveyForm }) {
  const qc = useQueryClient()
  const [view, setView] = useState<'wizard' | 'done'>(initial.completed ? 'done' : 'wizard')
  const [step, setStep] = useState<number>(initial.completed ? 1 : (initial.next_step ?? 1))
  const [draft, setDraft] = useState<Draft>(() => draftFrom(initial))
  const [done, setDone] = useState<SurveyState | null>(initial.completed ? initial : null)
  const [error, setError] = useState<string | null>(null)

  const patch = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }))

  const save = useMutation({
    mutationFn: async (target: number): Promise<SurveyState> => {
      switch (target) {
        case 1:
          return unwrap(api.PUT('/api/survey/steps/industries', { body: { industries: draft.industries } }))
        case 2:
          return unwrap(api.PUT('/api/survey/steps/actions', { body: { actions: draft.actions } }))
        case 3:
          return unwrap(
            api.PUT('/api/survey/steps/stack', {
              body: {
                technologies: draft.technologies,
                experience_months: experienceMonths(draft),
                roles: draft.roles,
                self_assessment: draft.selfAssessment,
              },
            }),
          )
        default:
          return unwrap(api.PUT('/api/survey/steps/specialization', { body: { chosen_spec: draft.chosenSpec ?? '' } }))
      }
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.survey, data)
      void qc.invalidateQueries({ queryKey: qk.qualifications })
    },
  })

  async function next() {
    setError(null)
    try {
      const data = await save.mutateAsync(step)
      if (step === 4) {
        setDone(data)
        setView('done')
      } else {
        setStep(step + 1)
        window.scrollTo({ top: 0 })
      }
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function back() {
    // изменения текущего шага не теряем: сохраняем тихо и возвращаемся
    setError(null)
    try {
      if (step < 4) await save.mutateAsync(step)
    } catch {
      /* возврат важнее: ошибка сохранения не должна запирать кандидата */
    }
    setStep(Math.max(1, step - 1))
    window.scrollTo({ top: 0 })
  }

  if (view === 'done' && done) {
    return (
      <SurveyDone
        state={done}
        onEdit={() => {
          setDraft(draftFrom(done))
          setStep(1)
          setView('wizard')
        }}
      />
    )
  }

  return (
    <div className="flex flex-1 flex-col">
      <StepTrace step={step} />
      <h1 className="mt-5 t-title text-brand">{STEP_TITLES[step - 1]}</h1>
      <p className="mt-1 t-small text-ink-3">
        Шаг {step} из 4 · сохраняем после каждого шага
      </p>

      <div className="mt-6 flex-1">
        {step === 1 && <StepIndustries form={form} draft={draft} patch={patch} />}
        {step === 2 && <StepActions form={form} draft={draft} patch={patch} />}
        {step === 3 && <StepStack form={form} draft={draft} patch={patch} />}
        {step === 4 && <StepSpec form={form} draft={draft} patch={patch} />}
      </div>

      {error && (
        <Alert tone="danger" className="mt-4">
          {error}
        </Alert>
      )}

      <div className="mt-8 flex flex-col gap-3">
        <Button block size="lg" loading={save.isPending} onClick={() => void next()} disabled={step === 4 && !draft.chosenSpec}>
          {step === 4 ? 'Завершить опрос' : 'Далее'}
        </Button>
        {step > 1 && (
          <Button variant="text" onClick={() => void back()} disabled={save.isPending}>
            Назад
          </Button>
        )}
      </div>
    </div>
  )
}

function StepTrace({ step }: { step: number }) {
  const frac = (i: number) => i / 3
  return (
    <Trace
      label={`Шаг ${step} из 4`}
      height={14}
      inset={6}
      nodes={[0, 1, 2, 3].map((i) => ({ at: frac(i), variant: i + 1 < step ? 'solid' : i + 1 === step ? 'current' : 'muted' }))}
      segments={[
        { from: 0, to: frac(step - 1) },
        { from: frac(step - 1), to: 1, dashed: true },
      ]}
    />
  )
}

// --- Шаг 1 -------------------------------------------------------------------------------------------------------

function StepIndustries({ form, draft, patch }: StepProps) {
  const toggle = (code: string) =>
    patch({ industries: draft.industries.includes(code) ? draft.industries.filter((c) => c !== code) : [...draft.industries, code] })
  return (
    <div className="flex flex-col gap-4">
      <p className="t-body text-ink-2">
        Где вам интересно работать? Это только фильтр для подбора: на уровень и категорию он не влияет. Можно ничего не выбирать.
      </p>
      <div className="flex flex-wrap gap-2.5" role="group" aria-label="Сфера бизнеса">
        {form.industries.map((i) => {
          const on = draft.industries.includes(i.code)
          return (
            <button
              key={i.code}
              type="button"
              role="checkbox"
              aria-checked={on}
              onClick={() => toggle(i.code)}
              className={cn(
                'rounded-pill border px-[18px] py-2.5 t-label transition-colors',
                on ? 'border-brand bg-brand text-white' : 'border-steel bg-white text-ink hover:border-brand hover:text-brand',
              )}
            >
              {i.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// --- Шаг 2 -------------------------------------------------------------------------------------------------------

function StepActions({ form, draft, patch }: StepProps) {
  const options = form.frequency_levels.map((l) => ({ value: l.code as Freq, label: l.label }))
  return (
    <div className="flex flex-col gap-6">
      <p className="t-body text-ink-2">Что вы делали руками за последние 12 месяцев? Неотмеченное считаем как «не делал».</p>
      <ul className="flex flex-col gap-5">
        {form.actions.map((a) => (
          <li key={a.code} className="flex flex-col gap-2.5">
            <p id={`act-${a.code}`} className="t-body text-ink">
              {a.label}
            </p>
            <Segmented
              aria-label={a.label}
              block
              options={options}
              value={draft.actions[a.code] ?? null}
              onChange={(v) => patch({ actions: { ...draft.actions, [a.code]: v } })}
              className="gap-2 [&_button]:px-3"
            />
          </li>
        ))}
      </ul>
    </div>
  )
}

// --- Шаг 3 -------------------------------------------------------------------------------------------------------

const SELF_LABELS = ['не знаком', 'базово', 'уверенно', 'сильно']

function StepStack({ form, draft, patch }: StepProps) {
  const [roleInput, setRoleInput] = useState('')
  const toggleTech = (code: string) => {
    const t = { ...draft.technologies }
    if (code in t) delete t[code]
    else t[code] = 'now'
    patch({ technologies: t })
  }
  const addRole = () => {
    const v = roleInput.trim()
    if (!v || draft.roles.includes(v) || draft.roles.length >= 10) return
    patch({ roles: [...draft.roles, v] })
    setRoleInput('')
  }
  const selectedTech = form.technologies.filter((t) => t.code in draft.technologies)

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <h2 className="t-name text-ink">С чем работали</h2>
        <div className="flex flex-wrap gap-2.5" role="group" aria-label="Технологии">
          {form.technologies.map((t) => {
            const on = t.code in draft.technologies
            return (
              <button
                key={t.code}
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => toggleTech(t.code)}
                className={cn(
                  'rounded-pill border px-4 py-2 t-label transition-colors',
                  on ? 'border-brand bg-brand text-white' : 'border-steel bg-white text-ink hover:border-brand hover:text-brand',
                )}
              >
                {t.label}
              </button>
            )
          })}
        </div>
        {selectedTech.length > 0 && (
          <ul className="mt-1 flex flex-col gap-3">
            {selectedTech.map((t) => (
              <li key={t.code} className="grid grid-cols-[1fr_auto] items-center gap-3">
                <label htmlFor={`rec-${t.code}`} className="min-w-0 t-body text-ink">
                  {t.label}
                </label>
                <select
                  id={`rec-${t.code}`}
                  value={draft.technologies[t.code]}
                  onChange={(e) => patch({ technologies: { ...draft.technologies, [t.code]: e.target.value } })}
                  className={cn(fieldBorderClasses(false), 'h-10 w-auto max-w-[200px] px-3 t-small')}
                >
                  {form.recency_levels.map((r) => (
                    <option key={r.code} value={r.code}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="t-name text-ink">Стаж в ИТ</h2>
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Лет"
            inputMode="numeric"
            type="number"
            min={0}
            max={50}
            value={draft.years}
            onChange={(e) => patch({ years: e.target.value })}
            placeholder="0"
          />
          <Input
            label="Месяцев"
            inputMode="numeric"
            type="number"
            min={0}
            max={11}
            value={draft.months}
            onChange={(e) => patch({ months: e.target.value })}
            placeholder="0"
          />
        </div>
        <p className="t-small text-ink-3">Это подсказка при выборе грейда: категорию определяет только тест.</p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="t-name text-ink">Роли в команде</h2>
        <div className="flex gap-2">
          <Input
            aria-label="Роль в команде"
            wrapperClassName="flex-1"
            value={roleInput}
            onChange={(e) => setRoleInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addRole()
              }
            }}
            placeholder="Например, техлид"
            maxLength={60}
          />
          <Button variant="outline" onClick={addRole} disabled={!roleInput.trim()} className="h-12 shrink-0">
            Добавить
          </Button>
        </div>
        {draft.roles.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {draft.roles.map((r) => (
              <li key={r}>
                <button
                  type="button"
                  onClick={() => patch({ roles: draft.roles.filter((x) => x !== r) })}
                  aria-label={`Убрать роль «${r}»`}
                  className="inline-flex items-center gap-1.5 rounded-pill bg-panel px-3 py-1.5 t-small text-ink hover:bg-hairline"
                >
                  {r}
                  <span aria-hidden="true" className="text-ink-3">
                    ×
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <div>
          <h2 className="t-name text-ink">Самооценка по модулям</h2>
          <p className="mt-1 t-small text-ink-3">От 0 («не знаком») до 3 («сильно»). Попадёт только в серые чипы «заявлено», в оценку не входит.</p>
        </div>
        <ul className="flex flex-col gap-4">
          {form.modules.map((m) => {
            const v = draft.selfAssessment[m.code] ?? 0
            return (
              <li key={m.code} className="flex flex-col gap-2">
                <p className="t-body text-ink">
                  {m.label} <span className="t-small text-ink-3">· {SELF_LABELS[v]}</span>
                </p>
                <Segmented
                  aria-label={`Самооценка: ${m.label}`}
                  block
                  options={[0, 1, 2, 3].map((n) => ({ value: String(n), label: String(n) }))}
                  value={m.code in draft.selfAssessment ? String(v) : null}
                  onChange={(s) => patch({ selfAssessment: { ...draft.selfAssessment, [m.code]: Number(s) } })}
                  className="gap-2 [&_button]:px-0"
                />
              </li>
            )
          })}
        </ul>
      </section>
    </div>
  )
}

// --- Шаг 4 -------------------------------------------------------------------------------------------------------

const STATUS_CHIP: Record<string, { label: string; tone: 'tint' | 'panel' }> = {
  active: { label: 'Тест доступен', tone: 'tint' },
  survey_only: { label: 'Тест скоро', tone: 'panel' },
  derived: { label: 'Обе составляющие', tone: 'panel' },
}

function StepSpec({ form, draft, patch }: StepProps) {
  const chosen = draft.chosenSpec
  const hint = useQuery({
    queryKey: ['candidate', 'survey', 'hint', chosen],
    queryFn: () => unwrap(api.POST('/api/survey/hint', { body: { chosen_spec: chosen ?? '' } })),
    enabled: !!chosen,
    staleTime: 60_000,
  })

  return (
    <div className="flex flex-col gap-5">
      <p className="t-body text-ink-2">Выберите направление, по которому хотите получить категорию. Подсказка классификатора ни к чему не обязывает: решаете вы.</p>
      <div role="radiogroup" aria-label="Направление" className="flex flex-col gap-2.5">
        {form.specializations.map((s) => {
          const on = chosen === s.code
          const chip = STATUS_CHIP[s.status] ?? STATUS_CHIP.active
          return (
            <button
              key={s.code}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => patch({ chosenSpec: s.code })}
              className={cn(
                'flex items-center justify-between gap-3 rounded-panel-lg border px-4 py-3.5 text-left transition-colors',
                on ? 'border-brand bg-tint' : 'border-hairline bg-white hover:border-steel',
              )}
            >
              <span className="t-label text-ink">{s.label}</span>
              <Chip tone={on ? 'tint' : chip.tone} className="shrink-0 whitespace-nowrap">
                {chip.label}
              </Chip>
            </button>
          )
        })}
      </div>

      {chosen && hint.data && (
        <div className="flex flex-col gap-3">
          {hint.data.hint && hint.data.hint_text && (
            <Panel variant="tint" radius="lg" padding="sm" className="flex flex-col gap-2">
              <p className="t-body text-ink">{hint.data.hint_text}</p>
              <div>
                <Button variant="outline" size="sm" onClick={() => patch({ chosenSpec: hint.data.top })}>
                  Выбрать «{hint.data.top_name}»
                </Button>
              </div>
            </Panel>
          )}
          {hint.data.waitlist_text && <Alert>{hint.data.waitlist_text}</Alert>}
          {hint.data.derived_text && <Alert>{hint.data.derived_text}</Alert>}
          <div>
            <p className="mb-2 t-small text-ink-3">Что показал классификатор по вашим ответам</p>
            <ProbabilityBar
              label="Вероятности направлений"
              parts={hint.data.probabilities.slice(0, 3).map((p) => ({
                name: p.name,
                percent: Math.round(p.probability * 100),
                active: p.code === chosen,
              }))}
            />
          </div>
        </div>
      )}
    </div>
  )
}

interface StepProps {
  form: SurveyForm
  draft: Draft
  patch: (p: Partial<Draft>) => void
}

// --- Итог --------------------------------------------------------------------------------------------------------

function SurveyDone({ state, onEdit }: { state: SurveyState; onEdit: () => void }) {
  const c = state.classification
  const spec = c?.chosen ?? state.answers.chosen_spec ?? ''

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="t-title text-brand">Опрос готов</h1>
        {c && <p className="mt-2 t-lead text-ink">Направление: {c.chosen_name}</p>}
      </div>

      {c?.hint && c.hint_text && (
        <Panel variant="tint" radius="lg" padding="sm">
          <p className="t-small text-ink-2">{c.hint_text}</p>
        </Panel>
      )}

      {c && c.declared_modules.length > 0 && (
        <section>
          <p className="mb-2 t-small text-ink-3">Заявлено вами — серым, в оценку не входит</p>
          <ul className="flex flex-wrap gap-2">
            {c.declared_modules.map((m) => (
              <li key={m.code}>
                <SkillChip name={m.label} kind="claimed" />
              </li>
            ))}
          </ul>
        </section>
      )}

      {c?.testable && c.chosen_status === 'active' && (
        <div className="flex flex-col gap-3">
          {c.suggested_grade_text && <p className="t-body text-ink-2">{c.suggested_grade_text}</p>}
          <Button block size="lg" to={`/candidate/grade?spec=${spec}`}>
            Выбрать грейд
          </Button>
        </div>
      )}

      {c?.chosen_status === 'derived' && (
        <div className="flex flex-col gap-3">
          <Alert>{c.derived_text ?? 'Категория Fullstack присваивается, когда подтверждены обе составляющие.'}</Alert>
          <Button block size="lg" to="/candidate/grade?spec=backend_python">
            Начать с Backend (Python)
          </Button>
          <Button block variant="outline" to="/candidate/grade?spec=frontend_js">
            Начать с Frontend (JS/TS)
          </Button>
        </div>
      )}

      {c?.chosen_status === 'survey_only' && (
        <div className="flex flex-col gap-3">
          <Panel variant="tint" radius="lg" padding="md" className="flex flex-col gap-2">
            <p className="t-name text-ink">Вы в листе ожидания</p>
            <p className="t-body text-ink-2">{c.waitlist_text ?? state.message ?? 'Тест по этому направлению ещё готовится.'}</p>
            <p className="t-small text-ink-3">
              Пока можно привязать ФСП ID и заполнить профиль — это пригодится, когда тест появится. Отсутствие истории ФСП — не штраф.
            </p>
          </Panel>
          <Button block variant="outline" to="/candidate/fsp">
            ФСП ID и достижения
          </Button>
          <Button block variant="outline" to="/candidate/profile/edit">
            Профиль и резюме
          </Button>
        </div>
      )}

      <div className="flex flex-col items-center gap-2">
        <Button variant="text" onClick={onEdit}>
          Изменить ответы опроса
        </Button>
        <Link to="/candidate" className="t-small text-ink-3 underline-offset-4 hover:underline">
          На главный экран
        </Link>
      </div>
    </div>
  )
}
