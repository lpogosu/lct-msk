import type { CategoryCard, CategoryRec } from '../api'
import { CATEGORY_KIND_LABEL } from '../dict'
import { catKey } from '../reach'
import { cn } from '@/lib/cn'
import { countWord, formatInt } from '@/lib/format'
import { Checkbox } from '@/ui'
import { Tag } from './common'

type Cat = CategoryRec & Partial<Pick<CategoryCard, 'share_fsp' | 'share_active' | 'reach'>>

const pct = (v: number) => (v > 0 && v < 0.01 ? '<1%' : `${formatInt(Math.round(v * 100))}%`)

/**
 * Рекомендованные категории (Р4): основная, предложенные и смежная — с вероятностью, числом кандидатов,
 * долей с ФСП и активных. Выбранные категории попадут в подборку; «по опросу» (без теста) ждут запуска теста.
 */
export function CategoriesPanel({
  categories,
  selected,
  onToggle,
  salaryTo,
  focusKey,
  onFocus,
}: {
  categories: Cat[]
  selected: Set<string>
  /** Без обработчика — только чтение (бриф ещё не сохранён) */
  onToggle?: (key: string) => void
  salaryTo?: number | null
  focusKey?: string | null
  onFocus?: (key: string) => void
}) {
  if (categories.length === 0) return null
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0">
      {categories.map((c) => {
        const key = catKey(c)
        const survey = c.status === 'survey_only'
        const lines: string[] = []
        if (c.n != null) {
          lines.push(survey ? `Лист ожидания: ${formatInt(c.n)}` : countWord(c.n, ['кандидат', 'кандидата', 'кандидатов']))
        }
        if (!survey && c.share_fsp != null) lines.push(`${pct(c.share_fsp)} с ФСП`)
        if (!survey && c.share_active != null) lines.push(`${pct(c.share_active)} активны за месяц`)
        if (!survey && c.reach?.shown && c.reach.reach_at_to != null && salaryTo != null) {
          lines.push(`вилка покрывает ${c.reach.reach_at_to} из ${c.reach.n}`)
        }
        const focused = focusKey === key
        return (
          <li
            key={key}
            className={cn(
              'rounded-panel border px-4 py-2.5 transition-colors',
              focused ? 'border-brand bg-white' : 'border-hairline bg-white',
            )}
          >
            <div className="flex items-start justify-between gap-3">
              {onToggle ? (
                <Checkbox
                  checked={selected.has(key)}
                  disabled={survey}
                  onChange={() => onToggle(key)}
                  wrapperClassName="min-w-0 flex-1"
                  label={<span className="t-label text-ink">{c.name}</span>}
                  description={
                    <>
                      {lines.join(' · ')}
                      {survey && ' · подборка появится после запуска теста'}
                    </>
                  }
                />
              ) : (
                <div className="min-w-0 flex-1 py-1">
                  <p className="t-label text-ink">{c.name}</p>
                  {lines.length > 0 && <p className="t-small text-ink-3">{lines.join(' · ')}</p>}
                  {survey && <p className="t-small text-ink-3">Подборка появится после запуска теста по этому направлению.</p>}
                </div>
              )}
              <div className="flex shrink-0 flex-col items-end gap-1 pt-1">
                <Tag tone={c.kind === 'primary' ? 'brand' : 'default'}>
                  {CATEGORY_KIND_LABEL[c.kind] ?? c.kind} · {pct(c.p)}
                </Tag>
                {onFocus && c.reach?.shown && (
                  <button
                    type="button"
                    onClick={() => onFocus(key)}
                    aria-pressed={focused}
                    className="t-small text-brand underline-offset-4 hover:underline"
                  >
                    {focused ? 'охват показан' : 'показать охват'}
                  </button>
                )}
              </div>
            </div>
            {c.note && <p className="mt-1 t-small text-ink-2">{c.note}</p>}
            {c.reach && !c.reach.shown && c.reach.note && !survey && <p className="mt-1 t-small text-ink-3">{c.reach.note}</p>}
          </li>
        )
      })}
    </ul>
  )
}
