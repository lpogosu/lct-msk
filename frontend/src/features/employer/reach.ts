import type { CategoryCard } from './api'

export const catKey = (c: { spec: string; grade: string }) => `${c.spec}:${c.grade}`

/**
 * Охват при заданном верхе вилки — по точкам «верх вилки → охват» из бэкенда (шаг гистограммы, приросты не меньше
 * 3 человек: так нельзя вычислить минимум отдельного кандидата). Левее первой точки охват неизвестен — null.
 */
export function reachAt(card: CategoryCard, upper: number | null): number | null {
  if (upper == null) return card.reach.reach_at_to
  const pts = card.reach.points
  let r: number | null = null
  for (const p of pts) if (p.upper <= upper) r = p.reach
  return r
}

/** Границы ползунка вилки по точкам охвата: от первой до последней точки бэкенда (0,5× … 2× от низа вилки). */
export function reachSpan(card: CategoryCard | undefined): { min: number; max: number } | null {
  const pts = card?.reach.points ?? []
  if (pts.length === 0) return null
  return { min: pts[0].upper, max: pts[pts.length - 1].upper }
}
