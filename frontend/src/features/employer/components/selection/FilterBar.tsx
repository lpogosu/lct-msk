import { useState } from 'react'
import { Checkbox } from '@/ui'
import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'
import type { CategoryCard, VersionView } from '../../api'
import { FILTER_TOGGLE_LABEL, GRADE_LABEL, SPEC_LABEL, type ToggleFilterCode } from '../../dict'
import { catKey } from '../../reach'

const PRIMARY: ToggleFilterCode[] = ['must_confirmed', 'has_fsp']
const MORE: ToggleFilterCode[] = ['salary_fit', 'format', 'city', 'active', 'confirmed_only']

/**
 * Фильтры подборки как текстовые переключатели (Figma 27:2): грейды-категории с числом кандидатов и
 * «только подтверждённые навыки», «с ФСП». Каждое переключение собирает новую версию (v2, v3…);
 * фильтр никого не удаляет — отфильтрованные уходят в «Скрыто фильтрами».
 */
export function FilterBar({
  view,
  categories,
  disabled,
  onCategories,
  onToggleFilter,
}: {
  view: VersionView
  categories: CategoryCard[]
  disabled: boolean
  onCategories: (keys: string[]) => void
  onToggleFilter: (code: ToggleFilterCode, on: boolean) => void
}) {
  const [more, setMore] = useState(false)
  const selected = new Set(view.categories.map(catKey))
  const testable = categories.filter((c) => c.status !== 'survey_only')
  const onFilters = new Set(view.filters.map((f) => f.code))
  const sameSpec = new Set(testable.map((c) => c.spec)).size <= 1
  const moreActive = MORE.some((c) => onFilters.has(c))

  const toggleCat = (k: string) => {
    const next = new Set(selected)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    onCategories([...next])
  }

  return (
    <div className="flex flex-col gap-2" role="group" aria-label="Фильтры подборки">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
        {testable.map((c) => {
          const k = catKey(c)
          const on = selected.has(k)
          const label = sameSpec ? (GRADE_LABEL[c.grade] ?? c.grade) : `${SPEC_LABEL[c.spec] ?? c.spec} · ${GRADE_LABEL[c.grade] ?? c.grade}`
          return (
            <Checkbox
              key={k}
              checked={on}
              muted={!on}
              disabled={disabled || (on && selected.size === 1)}
              title={on && selected.size === 1 ? 'Должна остаться хотя бы одна категория' : undefined}
              onChange={() => toggleCat(k)}
              wrapperClassName="shrink-0"
              label={
                <span>
                  {label}
                  {c.n != null && <span className="ml-1.5 font-mono tabular-nums">{formatInt(c.n)}</span>}
                </span>
              }
            />
          )
        })}
        {PRIMARY.map((code) => (
          <Checkbox
            key={code}
            checked={onFilters.has(code)}
            muted={!onFilters.has(code)}
            disabled={disabled}
            onChange={(e) => onToggleFilter(code, e.target.checked)}
            wrapperClassName="shrink-0"
            label={FILTER_TOGGLE_LABEL[code]}
          />
        ))}
        <button
          type="button"
          aria-expanded={more}
          onClick={() => setMore((v) => !v)}
          className={cn('t-label underline-offset-4 hover:underline', moreActive ? 'text-brand' : 'text-ink-3 hover:text-ink')}
        >
          {more ? 'Меньше фильтров' : moreActive ? 'Ещё фильтры (включены)' : 'Ещё фильтры'}
        </button>
      </div>
      {more && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
          {MORE.map((code) => (
            <Checkbox
              key={code}
              checked={onFilters.has(code)}
              muted={!onFilters.has(code)}
              disabled={disabled}
              onChange={(e) => onToggleFilter(code, e.target.checked)}
              wrapperClassName="shrink-0"
              label={FILTER_TOGGLE_LABEL[code]}
            />
          ))}
        </div>
      )}
    </div>
  )
}
