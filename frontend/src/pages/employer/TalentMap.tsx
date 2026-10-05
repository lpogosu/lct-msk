import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  useBank,
  useNeedCategories,
  useNeeds,
  useTalentMap,
  type BankItem,
  type MapCell,
} from '@/features/employer/api'
import { LoadError, Tag } from '@/features/employer/components/common'
import { ReachHistogram } from '@/features/employer/components/ReachHistogram'
import { GRADE_LABEL, GRADE_ORDER, MAP_SPECS, SPEC_LABEL } from '@/features/employer/dict'
import { cn } from '@/lib/cn'
import { countWord, formatInt, formatRubRangeK } from '@/lib/format'
import { Button, HeatCell, HeatLegend, heatLevel, Identicon, Illustration, Panel, Skeleton } from '@/ui'

// Направления, у которых тест есть всегда (config/taxonomy.yaml: active и derived). Остальные считаются «с тестом»,
// только если в банке уже есть подтверждённые тестом кандидаты (так подхватываются DevOps и QA после запуска теста).
const BASE_SPECS = ['backend_python', 'frontend_js', 'fullstack'] as const
const PROBE_SPECS = ['devops', 'qa'] as const
const SUBLABEL: Record<string, string> = { fullstack: 'обе квалификации' }

/** «Карта талантов» (Figma 33:2): специализация × грейд, цвет — число кандидатов, мини-трасса — доля с ФСП. */
export default function TalentMap() {
  const map = useTalentMap(BASE_SPECS, PROBE_SPECS, GRADE_ORDER)
  const [sel, setSel] = useState<{ spec: string; grade: string } | null>(null)

  const data = map.data
  const cellOf = useMemo(() => {
    const m = new Map<string, MapCell>()
    for (const c of data?.cells ?? []) m.set(`${c.spec}:${c.grade}`, c)
    return m
  }, [data])
  const total = (data?.cells ?? []).reduce((s, c) => s + c.n, 0)
  const withFsp = (data?.cells ?? []).reduce((s, c) => s + c.withFsp, 0)
  const max = Math.max(1, ...(data?.cells ?? []).map((c) => c.n))
  // Кандидат в банке показывается один раз — лучшей категорией, поэтому сумма ячеек = число специалистов с подтверждённым тестом
  const testedSpecs = data?.tested ?? [...BASE_SPECS]
  const waiting = MAP_SPECS.filter((s) => !testedSpecs.includes(s))
  const updated = data ? new Date(data.fetchedAt).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : null

  return (
    <div className="grid items-start gap-10 xl:grid-cols-[minmax(0,1fr)_420px] xl:gap-12">
      <div className="flex min-w-0 flex-col gap-10">
        <Panel variant="hero" radius="hero" padding="lg" className="relative overflow-hidden">
          <div className="grid items-center gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,300px)]">
            <div className="flex flex-col gap-4">
              <p className="t-small text-ink-3">{updated ? `Обновлено сегодня в ${updated}` : 'Считаем кандидатов…'}</p>
              <h1 className="t-display text-ink">
                {map.isPending ? '—' : formatInt(total)} специалистов
                <span className="block text-brand">подтвердили уровень тестом.</span>
              </h1>
              <p className="t-lead text-ink">
                Категории собраны из опроса и адаптивного теста, а не из текста резюме.
                {data && withFsp > 0 ? ` У ${formatInt(withFsp)} есть достижения ФСП.` : ''} Выберите ячейку — справа появятся кандидаты этой категории.
              </p>
            </div>
            <Illustration name="hero_vzaimno" alt="" className="hidden w-full md:block" eager />
          </div>
        </Panel>

        {map.isPending ? (
          <div className="flex flex-col gap-3" role="status" aria-label="Загрузка карты">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-[72px] w-full" />
            ))}
          </div>
        ) : map.isError ? (
          <LoadError error={map.error} onRetry={() => void map.refetch()} title="Не удалось собрать карту талантов" />
        ) : (
          <section aria-label="Карта талантов: специализация и грейд" className="flex flex-col gap-3">
            <div className="grid grid-cols-[minmax(110px,150px)_repeat(4,minmax(0,1fr))] items-end gap-x-2 gap-y-2 sm:gap-x-3 xl:grid-cols-[minmax(130px,200px)_repeat(4,minmax(0,1fr))]">
              <span />
              {GRADE_ORDER.map((g) => (
                <span key={g} className="t-small text-ink-3">
                  {GRADE_LABEL[g]}
                </span>
              ))}
              {testedSpecs.map((spec) => (
                <Row key={spec} spec={spec} cellOf={cellOf} max={max} sel={sel} onSelect={setSel} />
              ))}
            </div>
            {waiting.length > 0 && (
              <div className="flex flex-col gap-2">
                {waiting.map((spec) => (
                  <div key={spec} className="grid grid-cols-[minmax(110px,150px)_minmax(0,1fr)] items-center gap-x-2 sm:gap-x-3 xl:grid-cols-[minmax(130px,200px)_minmax(0,1fr)]">
                    <div>
                      <p className="t-body text-ink">{SPEC_LABEL[spec]}</p>
                      <p className="t-small text-ink-3">опрос, теста пока нет</p>
                    </div>
                    <p className="rounded-cell border border-dashed border-steel px-4 py-3 t-small text-ink-3">
                      Ждут теста. Подборки по этому направлению появятся после запуска теста.
                    </p>
                  </div>
                ))}
              </div>
            )}
            <HeatLegend className="pt-2" />
          </section>
        )}
      </div>

      <CategoryPanel sel={sel} cell={sel ? cellOf.get(`${sel.spec}:${sel.grade}`) : undefined} />
    </div>
  )
}

function Row({
  spec,
  cellOf,
  max,
  sel,
  onSelect,
}: {
  spec: string
  cellOf: Map<string, MapCell>
  max: number
  sel: { spec: string; grade: string } | null
  onSelect: (s: { spec: string; grade: string }) => void
}) {
  return (
    <>
      <div className="self-center">
        <p className="t-name text-ink">{SPEC_LABEL[spec] ?? spec}</p>
        <p className="t-small text-ink-3">{SUBLABEL[spec] ?? 'тест работает'}</p>
      </div>
      {GRADE_ORDER.map((g) => {
        const c = cellOf.get(`${spec}:${g}`)
        const n = c?.n ?? 0
        const isSel = sel?.spec === spec && sel.grade === g
        return (
          <HeatCell
            key={g}
            count={n}
            level={heatLevel(n, max)}
            fspShare={n > 0 && c ? c.withFsp / n : undefined}
            selected={isSel}
            disabled={n === 0}
            label={`${SPEC_LABEL[spec]}, ${GRADE_LABEL[g]}: ${countWord(n, ['кандидат', 'кандидата', 'кандидатов'])}`}
            onClick={() => onSelect({ spec, grade: g })}
            className={cn(n === 0 && 'cursor-default')}
          />
        )
      })}
    </>
  )
}

/** Правая панель выбранной ячейки: размер категории, ожидания по вилке (если есть потребность), сильнейшие по тесту, действия. */
function CategoryPanel({ sel, cell }: { sel: { spec: string; grade: string } | null; cell?: MapCell }) {
  const category = sel ? `${sel.spec}:${sel.grade}` : null
  const top = useBank({ category, hasFsp: null, page: 1 }, !!category)
  const needs = useNeeds()
  // ожидания кандидатов показываем по уже описанной потребности в этой же категории: гистограмма считается под её вилку
  const match = needs.data?.find((n) => n.specialization === sel?.spec && n.grade === sel?.grade && n.status !== 'closed')
  const cats = useNeedCategories(match?.id)
  const card = cats.data?.categories.find((c) => c.spec === sel?.spec && c.grade === sel?.grade)

  if (!sel) {
    return (
      <Panel variant="panel" radius="xl" padding="lg" className="xl:sticky xl:top-6">
        <div className="flex flex-col items-start gap-3">
          <h2 className="t-title text-ink">Выберите ячейку</h2>
          <p className="t-body text-ink-2">
            Нажмите на категорию на карте: здесь появятся число кандидатов, доля с ФСП, сильнейшие по тесту и ожидания по зарплате.
          </p>
          <Button variant="outline" to="/employer/needs/new">
            Описать потребность текстом
          </Button>
        </div>
      </Panel>
    )
  }

  const items: BankItem[] = (top.data?.items ?? []).slice(0, 3)
  return (
    <Panel variant="panel" radius="xl" padding="lg" className="flex flex-col gap-6 xl:sticky xl:top-6" aria-label="Выбранная категория">
      <div>
        <h2 className="t-title text-ink">
          {SPEC_LABEL[sel.spec]}
          <span className="block">{GRADE_LABEL[sel.grade]}</span>
        </h2>
        {cell && (
          <p className="mt-2 t-small text-ink-2">
            {countWord(cell.n, ['кандидат', 'кандидата', 'кандидатов'])}, {cell.withFsp} с достижениями ФСП
          </p>
        )}
      </div>

      {card?.reach.shown && card.reach.histogram ? (
        <section aria-label="Ожидания кандидатов" className="flex flex-col gap-3">
          <p className="t-small text-ink-3">Ожидания кандидатов, тыс. ₽{match?.salary_net ? ' на руки' : ''}</p>
          <ReachHistogram data={card.reach.histogram} from={match?.salary_from} to={match?.salary_to} height={90} />
          {match && card.reach.reach_at_to != null && (
            <p className="t-body text-ink-2">
              <span className="t-num-m mr-2 text-brand">
                {card.reach.reach_at_to} из {card.reach.n}
              </span>
              устроит вилка {formatRubRangeK(match.salary_from, match.salary_to)}
            </p>
          )}
        </section>
      ) : (
        <p className="t-small text-ink-3">
          Ожидания по зарплате покажем под вилку вашей потребности: опишите её для этой категории — и здесь появится охват.
        </p>
      )}

      <section aria-label="Сильнейшие по тесту" className="flex flex-col gap-2">
        <h3 className="t-body-strong text-ink">Сильнейшие по тесту</h3>
        {top.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : top.isError ? (
          <LoadError error={top.error} onRetry={() => void top.refetch()} title="Не удалось загрузить кандидатов" />
        ) : items.length === 0 ? (
          <p className="t-small text-ink-3">В этой категории пока никого нет.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
            {items.map((it) => (
              <li key={it.ref} className="flex items-center gap-3 py-3">
                <Identicon seed={it.pseudonym} size={28} />
                <div className="min-w-0 flex-1">
                  <p className="t-label text-ink">{it.pseudonym}</p>
                  <p className="line-clamp-1 t-small text-ink-2">{it.fsp_line ?? it.freshness?.label ?? ''}</p>
                </div>
                <Tag tone="brand">{it.test.band ?? 'тест пройден'}</Tag>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="flex flex-col gap-3">
        <Button block to={`/employer/bank/list?category=${category}`}>
          Открыть подборку категории
        </Button>
        <Button block variant="outline" to="/employer/needs/new">
          Описать потребность текстом
        </Button>
      </div>
      <p className="t-small text-ink-3">
        Контакты кандидатов откроются, только когда они ответят «да».{' '}
        <Link to="/employer/needs" className="text-brand underline-offset-4 hover:underline">
          Мои потребности
        </Link>
      </p>
    </Panel>
  )
}
