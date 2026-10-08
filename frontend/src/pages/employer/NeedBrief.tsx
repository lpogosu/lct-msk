import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import {
  parseBrief,
  qk,
  useInvalidateNeed,
  useNeed,
  useNeedCategories,
  useNeedCheck,
  type BriefParseIn,
  type CategoryCard,
  type Need,
  type ParseOut,
} from '@/features/employer/api'
import { buildMeaningRows, marksFromRows, salaryState } from '@/features/employer/brief'
import { BriefEditor } from '@/features/employer/components/BriefEditor'
import { CategoriesPanel } from '@/features/employer/components/CategoriesPanel'
import { CheckPanel } from '@/features/employer/components/CheckPanel'
import { BlockTitle, LoadError, PageHead, PageSkeleton } from '@/features/employer/components/common'
import { MarketHint } from '@/features/employer/components/MarketHint'
import { MeaningTrace } from '@/features/employer/components/MeaningTrace'
import { ReachHistogram } from '@/features/employer/components/ReachHistogram'
import { SalaryFields, SalaryRangeSlider } from '@/features/employer/components/SalaryRange'
import { SkillChips } from '@/features/employer/components/SkillChips'
import { gradeLabel } from '@/features/employer/dict'
import { catKey, reachAt } from '@/features/employer/reach'
import { formatInt, formatRub } from '@/lib/format'
import { Alert, Button, Checkbox, Input, Panel, Segmented, useToast } from '@/ui'

const TEXT_MAX = 3000 // BriefParseIn.text: max_length

interface Edits {
  spec?: string
  grade?: BriefParseIn['grade']
  must?: string[]
  nice?: string[]
}

interface SalaryDraft {
  from: number | null
  to: number | null
  net: boolean
  /** Работодатель правил числа сам. Пока нет — вилка берётся из текста брифа. */
  touched: boolean
}

/** Р3+Р4: «Опишите, кого ищете» — бриф слева, «Как мы поняли», категории и «Как мы проверим» справа. */
export default function NeedBrief() {
  const { id } = useParams()
  const need = useNeed(id)

  if (!id) return <BriefForm need={null} />
  if (need.isPending) return <PageSkeleton rows={3} />
  if (need.isError) return <LoadError error={need.error} onRetry={() => void need.refetch()} />
  return <BriefForm key={need.data.id} need={need.data} />
}

function BriefForm({ need }: { need: Need | null }) {
  const navigate = useNavigate()
  const toast = useToast()
  const qc = useQueryClient()
  const invalidate = useInvalidateNeed(need?.id)
  const categoriesQ = useNeedCategories(need?.id)
  const checkQ = useNeedCheck(need?.id)

  const [text, setText] = useState(need?.text ?? '')
  const [title, setTitle] = useState(need?.title ?? '')
  const [internship, setInternship] = useState(need?.is_internship ?? false)
  const [edits, setEdits] = useState<Edits>({})
  const [salary, setSalary] = useState<SalaryDraft>(() => ({
    from: need?.salary_from ?? null,
    to: need?.salary_to ?? null,
    net: need?.salary_net ?? true,
    touched: !!need,
  }))
  const [parse, setParse] = useState<ParseOut | null>(need?.parse ?? null)
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [focusCat, setFocusCat] = useState<string | null>(null)
  const [pickedCats, setPickedCats] = useState<Set<string> | null>(null)
  const [busy, setBusy] = useState<'build' | 'save' | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [attempted, setAttempted] = useState(false)
  const [anchor, setAnchor] = useState<{ from: number; to: number } | null>(
    need ? { from: need.salary_from, to: need.salary_to } : null,
  )

  // Живой разбор (≤ 300 мс на бэкенде): после паузы в наборе, без сохранения. Первый показ у сохранённого брифа не нужен.
  const firstRun = useRef(!!need)
  const parseBody = useMemo<BriefParseIn>(
    () => ({
      text,
      clear_city: false,
      ...(salary.touched ? { salary_from: salary.from, salary_to: salary.to, salary_net: salary.net } : {}),
      ...edits,
    }),
    [text, salary, edits],
  )
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false
      return
    }
    if (parseBody.text.trim().length < 3) {
      setParse(null)
      return
    }
    const ctrl = new AbortController()
    const t = setTimeout(async () => {
      setParsing(true)
      try {
        const p = await parseBrief(parseBody, ctrl.signal)
        if (ctrl.signal.aborted) return
        setParse(p)
        setParseError(null)
        const ps = p.salary
        if (ps?.salary_from && ps.salary_to) setAnchor((a) => a ?? { from: ps.salary_from as number, to: ps.salary_to as number })
      } catch (e) {
        if (ctrl.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        setParseError(errorMessage(e))
      } finally {
        if (!ctrl.signal.aborted) setParsing(false)
      }
    }, 500)
    return () => {
      clearTimeout(t)
      ctrl.abort()
    }
  }, [parseBody])

  // Вилка: свои числа или то, что нашли в тексте
  const eff = useMemo(() => {
    if (salary.touched) return { from: salary.from, to: salary.to, net: salary.net }
    const s = parse?.salary
    return { from: s?.salary_from ?? null, to: s?.salary_to ?? null, net: s?.net ?? salary.net }
  }, [salary, parse])
  const local = salaryState(eff.from, eff.to)
  const serverCheck = parse?.salary_check
  const salaryMsg = serverCheck?.message ?? null
  const salaryBlocked = local.state === 'blocked' || local.state === 'invalid' || serverCheck?.status === 'blocked' || serverCheck?.status === 'invalid'
  const salaryMissing = local.state === 'empty'
  const salaryError =
    salaryBlocked || (attempted && salaryMissing)
      ? (salaryMsg && salaryBlocked
          ? salaryMsg
          : local.state === 'blocked'
            ? 'Вилка шире чем в 2 раза: кандидаты не поймут, на что рассчитывать. Сузьте её.'
            : local.state === 'invalid'
              ? 'Проверьте вилку: «от» — не меньше 30 000 ₽, «до» — не меньше «от».'
              : 'Вилка обязательна: укажите «от» и «до» в рублях в месяц.')
      : null
  const salaryWarning =
    !salaryError && (local.state === 'warn' || serverCheck?.status === 'warn')
      ? (salaryMsg ?? 'Вилка шире чем в 1,6 раза. Можно сохранить, но кандидатам будет сложнее понять, на что рассчитывать.')
      : null

  // Категории: у сохранённого брифа — с охватом из бэкенда, у нового — рекомендации разбора
  const cards: CategoryCard[] | undefined = categoriesQ.data?.categories
  const selectedFromNeed = useMemo(
    () => new Set((need?.categories ?? []).filter((c) => c.selected).map(catKey)),
    [need],
  )
  const selected = pickedCats ?? selectedFromNeed
  const focusCard = useMemo(() => {
    if (!cards) return undefined
    return (
      cards.find((c) => catKey(c) === focusCat) ??
      cards.find((c) => selected.has(catKey(c)) && c.reach.shown) ??
      cards.find((c) => c.reach.shown)
    )
  }, [cards, focusCat, selected])

  const reachNow = focusCard ? reachAt(focusCard, eff.to) : null

  const rows = useMemo(() => (parse ? buildMeaningRows(parse, text) : []), [parse, text])
  const marks = useMemo(() => marksFromRows(rows), [rows])

  // Один уточняющий вопрос: варианты ответа — одним нажатием
  const question = parse?.question ?? null
  // пока работодатель не ответил, ни один вариант не выбран: мы спрашиваем, а не угадываем
  const questionValue = question?.field === 'grade' ? (edits.grade ?? null) : question?.field === 'spec' ? (edits.spec ?? null) : null

  function body(): BriefParseIn & { title?: string; is_internship: boolean } {
    return {
      text: text.trim(),
      clear_city: false,
      salary_from: eff.from,
      salary_to: eff.to,
      salary_net: eff.net,
      title: title.trim() || undefined,
      is_internship: internship,
      ...edits,
    }
  }

  function describe(e: unknown): string {
    if (e instanceof ApiException) return e.message
    return errorMessage(e)
  }

  /** Сохраняет бриф: новый — POST /needs, существующий — PATCH (при уже собранной подборке бэкенд сам создаст версию). */
  async function persist(): Promise<{ id: string; need: Need }> {
    const b = body()
    if (!need) {
      const created = await unwrap(api.POST('/api/needs', { body: { ...b, text: b.text, title: b.title ?? null } }))
      void qc.invalidateQueries({ queryKey: qk.needs })
      return { id: created.id, need: created }
    }
    const res = await unwrap(
      api.PATCH('/api/needs/{need_id}', { params: { path: { need_id: need.id } }, body: { ...b, title: b.title ?? '' } }),
    )
    invalidate()
    return { id: res.need.id, need: res.need }
  }

  function precheck(): boolean {
    setAttempted(true)
    setSubmitError(null)
    if (text.trim().length < 3) {
      setSubmitError('Опишите, кого ищете: хотя бы пару предложений.')
      return false
    }
    if (salaryBlocked || salaryMissing) return false
    return true
  }

  async function saveDraft() {
    if (!precheck()) return
    setBusy('save')
    try {
      const saved = await persist()
      saved.need.warnings?.forEach((w) => toast.show({ message: w.message, title: 'Обратите внимание' }))
      if (!need) {
        toast.success('Черновик сохранён. Теперь видно охват вилки.')
        navigate(`/employer/needs/${saved.id}/edit`, { replace: true })
      } else {
        toast.success('Бриф сохранён')
        void qc.invalidateQueries({ queryKey: qk.selectionAll(need.id) })
      }
    } catch (e) {
      setSubmitError(describe(e))
    } finally {
      setBusy(null)
    }
  }

  async function showSelection() {
    if (!precheck()) return
    setBusy('build')
    try {
      const saved = await persist()
      saved.need.warnings?.forEach((w) => toast.show({ message: w.message, title: 'Обратите внимание' }))
      const chosen = pickedCats ?? null
      const changedCats = chosen && !sameSet(chosen, selectedFromNeed)
      const hasVersion = (saved.need.current_version ?? 0) > 0
      if (!hasVersion || changedCats) {
        const catsBody = chosen
          ? [...chosen].map((k) => {
              const [spec, grade] = k.split(':')
              return { spec, grade: grade as 'intern' | 'junior' | 'middle' | 'senior' }
            })
          : undefined
        await unwrap(
          api.POST('/api/needs/{need_id}/versions', { params: { path: { need_id: saved.id } }, body: { categories: catsBody } }),
        )
      }
      void qc.invalidateQueries({ queryKey: qk.needs })
      void qc.invalidateQueries({ queryKey: qk.selectionAll(saved.id) })
      void qc.invalidateQueries({ queryKey: qk.versions(saved.id) })
      navigate(`/employer/needs/${saved.id}`)
    } catch (e) {
      setSubmitError(describe(e))
    } finally {
      setBusy(null)
    }
  }

  const noCategorySelected = !!need && (pickedCats ?? selectedFromNeed).size === 0

  // Слайдер вилки: ось держим стабильной, пока тащим ползунок (она зависит от первой осмысленной вилки и охвата)
  const axis = useMemo(() => {
    const f = anchor?.from ?? 100_000
    const t = anchor?.to ?? 200_000
    const h = focusCard?.reach.histogram
    const histLo = h ? h.bins[0][0] - h.step : f
    const histHi = h ? h.bins[h.bins.length - 1][0] : t
    const lo = Math.max(30_000, Math.floor((Math.min(f, histLo) * 0.8) / 10_000) * 10_000)
    const hi = Math.ceil((Math.max(t, histHi) * 1.1) / 10_000) * 10_000
    return { lo, hi }
  }, [anchor, focusCard?.reach.histogram])

  const sliderReady = eff.from != null && eff.to != null && eff.from >= 30_000 && eff.to >= eff.from

  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,600px)] lg:gap-14">
      <div className="flex min-w-0 flex-col gap-8">
        <PageHead
          title={need ? 'Правка потребности' : 'Опишите, кого ищете'}
          lead="Пишите как коллеге. Мы покажем, как поняли каждое слово, и предложим категории кандидатов."
        />

        <BriefEditor
          value={text}
          onChange={setText}
          marks={marks}
          activeKey={activeKey}
          maxLength={TEXT_MAX}
          autoFocus={!need}
          placeholder="Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI, PostgreSQL, опыт от 2 лет. Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки."
          footerLeft={`${formatInt(text.length)} из ${formatInt(TEXT_MAX)} знаков`}
          footerRight={parsing ? 'разбираем…' : parseError ? <span className="text-danger-text">{parseError}</span> : 'разобрано правилами, без нейросети'}
          error={attempted && text.trim().length < 3 ? 'Опишите, кого ищете: хотя бы пару предложений.' : null}
        />

        {question && (
          <section className="flex flex-col gap-3" aria-labelledby="q-title">
            <BlockTitle>
              <span id="q-title">Уточните одну вещь</span>
            </BlockTitle>
            <p className="t-body text-ink-2">{question.text}</p>
            {(question.options ?? []).length > 0 && (question.field === 'grade' || question.field === 'spec') && (
              <Segmented
                aria-label={question.text}
                options={(question.options ?? []).map((o) => ({ value: o.code, label: o.name }))}
                value={questionValue ?? null}
                onChange={(v) =>
                  setEdits((e) => (question.field === 'grade' ? { ...e, grade: v as Edits['grade'] } : { ...e, spec: v }))
                }
              />
            )}
          </section>
        )}

        <section className="flex flex-col gap-4" aria-labelledby="salary-title">
          <BlockTitle>
            <span id="salary-title">Вилка и охват</span>
          </BlockTitle>
          <SalaryFields
            from={eff.from}
            to={eff.to}
            net={eff.net}
            onFrom={(v) => {
              setSalary({ from: v, to: eff.to, net: eff.net, touched: true })
              if (v && eff.to) setAnchor((a) => a ?? { from: v, to: eff.to as number })
            }}
            onTo={(v) => {
              setSalary({ from: eff.from, to: v, net: eff.net, touched: true })
              if (v && eff.from) setAnchor((a) => a ?? { from: eff.from as number, to: v })
            }}
            onNet={(v) => setSalary({ from: eff.from, to: eff.to, net: v, touched: true })}
            error={salaryError}
            warning={salaryWarning}
          />
          <MarketHint
            spec={edits.spec ?? parse?.spec?.top}
            grade={edits.grade ?? parse?.grade?.top}
            salaryTo={eff.to}
            net={eff.net}
          />
          {sliderReady && (
            <div className="flex flex-col gap-3">
              <SalaryRangeSlider
                min={Math.min(axis.lo, eff.from as number)}
                max={Math.max(axis.hi, eff.to as number)}
                step={5000}
                from={eff.from as number}
                to={eff.to as number}
                label="Вилка"
                onChange={(f, t) => setSalary({ from: f, to: t, net: eff.net, touched: true })}
              />
              {focusCard?.reach.shown && focusCard.reach.histogram && (
                <ReachHistogram
                  data={focusCard.reach.histogram}
                  from={eff.from}
                  to={eff.to}
                  height={72}
                />
              )}
            </div>
          )}
          <Coverage need={need} card={focusCard} reachNow={reachNow} loading={!!need && categoriesQ.isPending} categoriesError={categoriesQ.isError} />
        </section>

        <section className="flex flex-col gap-4" aria-labelledby="more-title">
          <BlockTitle>
            <span id="more-title">Название и тип</span>
          </BlockTitle>
          <Input
            label="Название потребности (необязательно)"
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Backend Middle в платёжный шлюз"
            hint="Кандидат увидит его в приглашении. Если пусто — возьмём название категории."
          />
          <Checkbox
            checked={internship}
            onChange={(e) => setInternship(e.target.checked)}
            label="Стажировка"
            description="Кандидаты 16–17 лет видны только таким потребностям."
          />
        </section>

        {submitError && (
          <Alert tone="danger" title="Не получилось сохранить">
            {submitError}
          </Alert>
        )}
        {noCategorySelected && <p className="t-small text-danger-text">Выберите хотя бы одну категорию справа.</p>}

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <Button size="lg" onClick={() => void showSelection()} loading={busy === 'build'} disabled={busy === 'save' || noCategorySelected}>
            Показать подборку
          </Button>
          <Button variant="text" onClick={() => void saveDraft()} loading={busy === 'save'} disabled={busy === 'build'}>
            {need ? 'Сохранить изменения' : 'Сохранить черновик'}
          </Button>
          {need && (
            <Button variant="text" to={`/employer/needs/${need.id}`}>
              К подборке
            </Button>
          )}
        </div>
      </div>

      <aside aria-label="Разбор брифа">
        <Panel variant="panel" radius="xl" padding="lg" className="flex flex-col gap-8 lg:sticky lg:top-6">
          <MeaningTrace rows={rows} loading={parsing} activeKey={activeKey} onActive={setActiveKey} />
          {parse && parse.skills.length > 0 && (
            <SkillChips
              skills={parse.skills}
              onChange={(must, nice) => setEdits((e) => ({ ...e, must, nice }))}
            />
          )}
          <section aria-labelledby="cat-title" className="flex flex-col gap-3">
            <BlockTitle>
              <span id="cat-title">Категории кандидатов</span>
            </BlockTitle>
            {need && categoriesQ.isPending ? (
              <p className="t-body text-ink-3">Считаем кандидатов в категориях…</p>
            ) : need && categoriesQ.isError ? (
              <LoadError error={categoriesQ.error} onRetry={() => void categoriesQ.refetch()} title="Не удалось посчитать категории" />
            ) : cards ? (
              <CategoriesPanel
                categories={cards}
                selected={selected}
                onToggle={(k) =>
                  setPickedCats((cur) => {
                    const next = new Set(cur ?? selectedFromNeed)
                    if (next.has(k)) next.delete(k)
                    else next.add(k)
                    return next
                  })
                }
                salaryTo={eff.to}
                focusKey={focusCard ? catKey(focusCard) : null}
                onFocus={setFocusCat}
              />
            ) : parse && parse.categories && parse.categories.length > 0 ? (
              <>
                <CategoriesPanel categories={parse.categories} selected={new Set()} />
                <p className="t-small text-ink-3">
                  Число кандидатов и охват вилки покажем после сохранения черновика. Основная категория выберется сама.
                </p>
              </>
            ) : (
              <p className="t-body text-ink-3">Категории предложим, когда будет понятно направление и уровень.</p>
            )}
            {need && parse && parse.grade.top && (
              <p className="t-small text-ink-3">
                Грейд по тексту: {gradeLabel(parse.grade.top)}. Добавьте соседний грейд, если хотите шире.
              </p>
            )}
          </section>
          <CheckPanel parse={parse} check={checkQ.data} loading={!!need && checkQ.isPending} />
        </Panel>
      </aside>
    </div>
  )
}

function sameSet(a: Set<string>, b: Set<string>) {
  if (a.size !== b.size) return false
  for (const x of a) if (!b.has(x)) return false
  return true
}

/** «29 из 38 Middle-бэкендеров устроит эта вилка» — охват верха вилки в основной категории. */
function Coverage({
  need,
  card,
  reachNow,
  loading,
  categoriesError,
}: {
  need: Need | null
  card: CategoryCard | undefined
  reachNow: number | null
  loading: boolean
  categoriesError: boolean
}) {
  if (!need) {
    return (
      <p className="t-small text-ink-3">
        Охват вилки (сколько кандидатов она устроит) покажем после сохранения черновика: он считается по ожиданиям
        кандидатов в выбранной категории.
      </p>
    )
  }
  if (loading) return <p className="t-small text-ink-3">Считаем охват…</p>
  if (categoriesError || !card) return null
  if (!card.reach.shown) {
    return (
      <p className="t-small text-ink-3">
        {card.reach.note ?? 'Кандидатов в категории мало, охват не показываем.'} В категории «{card.name}» — {card.n}.
      </p>
    )
  }
  const shortName = card.name.replace(/\s*·\s*/, ' · ')
  return (
    <div className="flex flex-col gap-1">
      <p className="t-body text-ink-2">
        <span className="t-num-m mr-2 text-brand">
          {reachNow ?? '—'} из {card.reach.n}
        </span>
        кандидатов категории «{shortName}» устроит эта вилка.
        {card.reach.p25 != null && card.reach.p75 != null && (
          <> Половина ждёт от {formatRub(card.reach.p25)} до {formatRub(card.reach.p75)}.</>
        )}
      </p>
      {card.reach.hint && <p className="t-small text-ink-3">{card.reach.hint}</p>}
    </div>
  )
}
