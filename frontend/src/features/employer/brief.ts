import type { ParseOut } from './api'
import { FORMAT_LABEL_LOWER } from './dict'
import { formatDecimal, formatInt } from '@/lib/format'

// Разбор брифа → строки «Как мы поняли» и диапазоны подсветки цитат в тексте.
// Индексы цитат (start/end) в ответе бэкенда — в символах Unicode (кодовых точках Python), а не в единицах UTF-16 JS:
// поэтому текст режем через Array.from.

export const MODULE_LABEL: Record<string, string> = {
  python_core: 'Python',
  sql: 'SQL и базы данных',
  http_api: 'HTTP и API',
  algo: 'Алгоритмы и структуры данных',
  git_linux: 'Git и Linux',
  js_core: 'JavaScript / TypeScript',
  react: 'React',
  html_css: 'HTML и CSS',
  qa_testing: 'Тестирование ПО',
  devops_core: 'DevOps и инфраструктура',
}

export interface Range {
  start: number
  end: number
}

export interface MeaningRow {
  key: string
  /** Цитата из брифа (то, что написал работодатель) */
  label: string
  /** Как мы это поняли */
  meaning: string
  /** Неуверенное место — пунктир */
  uncertain: boolean
  ranges: Range[]
  /** Серый маркер («будут плюсом») подсвечивается серым, а не фиолетовым */
  marker?: boolean
}

function cps(text: string): string[] {
  return Array.from(text)
}

export function sliceText(text: string, r: Range): string {
  return cps(text).slice(r.start, r.end).join('')
}

function names(list: string[]): string {
  return list.join(', ')
}

/** Группирует чипы навыков по виду и фразе-маркеру: «Обязательно: Python, FastAPI, PostgreSQL». */
export function buildMeaningRows(parse: ParseOut, text: string): MeaningRow[] {
  const rows: MeaningRow[] = []
  const slice = (r: Range) => sliceText(text, r)

  // специализация
  const specQuotes = parse.spec.quotes ?? []
  const specManual = parse.spec.source !== 'правило'
  if (specQuotes.length > 0 || specManual) {
    const top = parse.spec.probs[0]
    rows.push({
      key: 'spec',
      label: specQuotes.length
        ? specQuotes
            .slice(0, 2)
            .map((q) => q.text)
            .join(', ')
        : 'ваш выбор',
      meaning: specManual
        ? `Специализация: ${parse.spec.top_name} (вы уточнили)`
        : `Специализация: ${parse.spec.top_name}${top ? ` · ${formatInt(Math.round(top.p * 100))}%` : ''}`,
      uncertain: !specManual && (top?.p ?? 1) < 0.6,
      ranges: specQuotes.slice(0, 2),
    })
  }

  // навыки: группа = вид + маркер
  const groups = new Map<string, { kind: 'must' | 'nice'; marker: Range | null; items: ParseOut['skills'] }>()
  for (const s of parse.skills) {
    const gk = `${s.kind}:${s.marker ? s.marker.start : 'none'}`
    const g = groups.get(gk) ?? { kind: s.kind, marker: s.marker ?? null, items: [] }
    g.items.push(s)
    groups.set(gk, g)
  }
  let gi = 0
  for (const g of groups.values()) {
    const quotes = g.items.map((s) => s.quote).filter((q): q is NonNullable<typeof q> => !!q)
    const nice = g.kind === 'nice'
    const markerText = g.marker ? slice(g.marker) : null
    const label = nice && markerText ? markerText : names(quotes.map((q) => q.text))
    rows.push({
      key: `skills-${gi++}`,
      label: label || names(g.items.map((s) => s.name)),
      meaning: `${nice ? 'Желательно' : 'Обязательно'}: ${names(g.items.map((s) => s.name))}`,
      uncertain: nice || g.items.some((s) => s.reason === 'default' || s.reason === 'overflow'),
      ranges: [...quotes, ...(nice && g.marker ? [g.marker] : [])],
      marker: false,
    })
  }

  // грейд
  const gradeQuotes = parse.grade.quotes ?? []
  const gradeManual = parse.grade.source !== 'правило'
  if (gradeQuotes.length > 0 || parse.experience_years || gradeManual) {
    const salaryQuotes = new Set((parse.salary?.quotes ?? []).map((q) => `${q.start}:${q.end}`))
    const quotes = gradeQuotes.filter((q) => !salaryQuotes.has(`${q.start}:${q.end}`))
    const [a, b] = parse.grade.probs
    const second = b && b.p >= 0.15 ? ` или ${b.name} ${formatDecimal(b.p, 2)}` : ''
    const first = a ? a.name : parse.grade.top_name
    rows.push({
      key: 'grade',
      label: gradeManual && gradeQuotes.length === 0 ? 'ваш выбор' : names((quotes.length ? quotes : gradeQuotes).slice(0, 2).map((q) => q.text)),
      meaning: gradeManual ? `Грейд: ${first} (вы уточнили)` : second && a ? `Грейд: ${first} ${formatDecimal(a.p, 2)}${second}` : `Грейд: ${first}`,
      uncertain: !gradeManual && (a?.p ?? 1) < 0.75,
      ranges: quotes.length ? quotes : gradeQuotes,
    })
  }

  // формат и город
  const fmtCity: { text: string; range: Range }[] = []
  for (const f of parse.formats) for (const q of f.quotes ?? []) fmtCity.push({ text: q.text, range: q })
  if (parse.city) for (const q of parse.city.quotes ?? []) fmtCity.push({ text: q.text, range: q })
  if (fmtCity.length > 0) {
    const parts: string[] = []
    if (parse.formats.length) parts.push(`формат ${parse.formats.map((f) => FORMAT_LABEL_LOWER[String(f.value)] ?? f.name ?? '').join(', ')}`)
    if (parse.city) parts.push(`город ${parse.city.name ?? String(parse.city.value)}`)
    rows.push({
      key: 'format',
      label: names(fmtCity.map((x) => x.text)),
      meaning: `${parts[0] ? parts[0][0].toUpperCase() + parts[0].slice(1) : ''}${parts[1] ? `, ${parts[1]}` : ''}`,
      uncertain: false,
      ranges: fmtCity.map((x) => x.range),
    })
  }

  if (parse.relocation) {
    rows.push({
      key: 'relocation',
      label: names((parse.relocation.quotes ?? []).map((q) => q.text)),
      meaning: `Релокация: ${parse.relocation.name ?? String(parse.relocation.value)}`,
      uncertain: true,
      ranges: parse.relocation.quotes ?? [],
    })
  }

  // вилка
  if (parse.salary && (parse.salary.salary_from || parse.salary.salary_to)) {
    const s = parse.salary
    const money =
      s.salary_from && s.salary_to
        ? `${formatInt(s.salary_from)} – ${formatInt(s.salary_to)} ₽`
        : `${formatInt((s.salary_from ?? s.salary_to) as number)} ₽`
    rows.push({
      key: 'salary',
      label: names((s.quotes ?? []).map((q) => q.text)).replace(', ', ' '),
      meaning: `${money}${s.net === true ? ' на руки' : s.net === false ? ' до НДФЛ' : ''}${s.yearly ? ' (в год, пересчитано)' : ''}`,
      uncertain: s.net == null,
      ranges: s.quotes ?? [],
    })
  }
  return rows
}

/** Диапазоны для подсветки: сначала всё распознанное, затем (поверх) — строка, на которую навели курсор. */
export interface Mark {
  start: number
  end: number
  rowKey: string
  marker: boolean
}

export function marksFromRows(rows: MeaningRow[]): Mark[] {
  return rows.flatMap((r) => r.ranges.map((q) => ({ start: q.start, end: q.end, rowKey: r.key, marker: r.marker === true })))
}

export interface Segment {
  text: string
  /** Распознана (подчёркивание) */
  mark: boolean
  /** Подсвечена наведением на строку «Как мы поняли» */
  active: boolean
}

/** Разбивает текст на сегменты по границам всех диапазонов. */
export function segmentText(text: string, marks: Mark[], activeKey: string | null): Segment[] {
  const chars = cps(text)
  const bounds = new Set<number>([0, chars.length])
  for (const m of marks) {
    bounds.add(Math.max(0, Math.min(chars.length, m.start)))
    bounds.add(Math.max(0, Math.min(chars.length, m.end)))
  }
  const sorted = [...bounds].sort((a, b) => a - b)
  const out: Segment[] = []
  for (let i = 0; i < sorted.length - 1; i++) {
    const from = sorted[i]
    const to = sorted[i + 1]
    if (to <= from) continue
    const covering = marks.filter((m) => m.start <= from && m.end >= to)
    out.push({
      text: chars.slice(from, to).join(''),
      mark: covering.length > 0,
      active: activeKey != null && covering.some((m) => m.rowKey === activeKey),
    })
  }
  return out
}

// --------------------------------------------------------------------------- вилка

// Зеркало config/contact.yaml → salary (бэкенд остаётся источником истины: он проверяет вилку при сохранении).
export const SALARY_MIN_FROM = 30_000
export const SALARY_STEP = 1_000
export const SALARY_SOFT_RATIO = 1.6
export const SALARY_HARD_RATIO = 2.0

export type SalaryState = 'empty' | 'ok' | 'warn' | 'blocked' | 'invalid'

export function salaryState(from: number | null, to: number | null): { state: SalaryState; ratio: number | null } {
  if (!from || !to) return { state: 'empty', ratio: null }
  if (from < SALARY_MIN_FROM || to < from) return { state: 'invalid', ratio: null }
  const ratio = to / from
  if (ratio > SALARY_HARD_RATIO) return { state: 'blocked', ratio }
  if (ratio > SALARY_SOFT_RATIO) return { state: 'warn', ratio }
  return { state: 'ok', ratio }
}

/** «220 000» → 220000; пусто или мусор → null */
export function parseMoney(value: string): number | null {
  const digits = value.replace(/\D/g, '')
  if (!digits) return null
  const n = Number(digits)
  return Number.isFinite(n) ? n : null
}
