// Шкала уровня для интерфейса: S = 500 + 100·θ, линейка 300–600.
// Значения повторяют config/taxonomy.yaml (score_scale и cut грейдов): стажёр θ≥−2 → 300, junior −1 → 400,
// middle 0 → 500, senior +1 → 600. Если справочник изменится — поправить здесь и в Ruler.

export const SCORE_MIN = 300
export const SCORE_MAX = 600

export type GradeCode = 'intern' | 'junior' | 'middle' | 'senior'

export interface Grade {
  code: GradeCode
  name: string
  /** Нижняя граница грейда на шкале 300–600 */
  cut: number
}

export const GRADES: readonly Grade[] = [
  { code: 'intern', name: 'Стажёр', cut: 300 },
  { code: 'junior', name: 'Junior', cut: 400 },
  { code: 'middle', name: 'Middle', cut: 500 },
  { code: 'senior', name: 'Senior', cut: 600 },
]

export function gradeByScore(score: number): Grade {
  let current = GRADES[0]
  for (const g of GRADES) if (score >= g.cut) current = g
  return current
}

/** Положение значения на линейке, 0…1 (с обрезкой по краям) */
export function scoreToRatio(score: number, min = SCORE_MIN, max = SCORE_MAX): number {
  if (max <= min) return 0
  return Math.min(1, Math.max(0, (score - min) / (max - min)))
}
