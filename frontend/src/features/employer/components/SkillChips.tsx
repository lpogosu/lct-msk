import { cn } from '@/lib/cn'
import type { ParseOut } from '../api'

const MUST_MAX = 5 // config/scoring.yaml: не больше 5 обязательных навыков (бэкенд ответит 422 brief_invalid)

type Skill = ParseOut['skills'][number]

/**
 * Правка навыков руками (источник «вручную» у бэкенда): сменить «обязательно / желательно» или убрать навык.
 * Правки переживают правку текста брифа.
 */
export function SkillChips({
  skills,
  onChange,
}: {
  skills: Skill[]
  onChange: (must: string[], nice: string[]) => void
}) {
  if (skills.length === 0) return null
  const must = skills.filter((s) => s.kind === 'must').map((s) => s.skill_id)
  const nice = skills.filter((s) => s.kind === 'nice').map((s) => s.skill_id)

  const toggle = (s: Skill) => {
    if (s.kind === 'must') onChange(must.filter((x) => x !== s.skill_id), [...nice, s.skill_id])
    else onChange([...must, s.skill_id], nice.filter((x) => x !== s.skill_id))
  }
  const remove = (s: Skill) =>
    onChange(
      must.filter((x) => x !== s.skill_id),
      nice.filter((x) => x !== s.skill_id),
    )

  return (
    <div className="flex flex-col gap-2">
      <p className="t-small text-ink-3">
        Навыки: нажмите на вид, чтобы сменить «обязательно» и «желательно». Обязательных — не больше {MUST_MAX}.
      </p>
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
        {skills.map((s) => (
          <li
            key={s.skill_id}
            className="flex items-center gap-1 rounded-pill border border-hairline bg-white py-1 pl-3 pr-1 t-small text-ink"
          >
            <span>{s.name}</span>
            <button
              type="button"
              onClick={() => toggle(s)}
              aria-label={`${s.name}: сейчас ${s.kind === 'must' ? 'обязательно' : 'желательно'}, сменить`}
              className={cn(
                'rounded-pill px-2 py-0.5 t-small',
                s.kind === 'must' ? 'bg-tint text-brand' : 'bg-panel text-ink-2',
              )}
            >
              {s.kind === 'must' ? 'обязательно' : 'желательно'}
            </button>
            <button
              type="button"
              onClick={() => remove(s)}
              aria-label={`Убрать навык ${s.name}`}
              className="flex h-6 w-6 items-center justify-center rounded-full text-ink-3 hover:bg-panel hover:text-ink"
            >
              <svg width="10" height="10" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
