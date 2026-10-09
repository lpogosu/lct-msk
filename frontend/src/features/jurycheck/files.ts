// Разбор выбранных файлов для «Свой набор»: что это за таблица и как её отправить на бэкенд.
//   .jsonl            — строки набора пар (как backend/eval/pairs_test.jsonl) → POST /api/admin/import
//   .json             — {vacancies, candidates, labels} простого формата или массив строк набора пар
//   .csv / .tsv / .txt — одна из таблиц простого формата; какая — по заголовку → POST /api/admin/import/simple
// Сами строки проверяет бэкенд (ошибки с номером строки и полем); здесь — только распознать файл и не упасть на JSON.

export type FileRole = 'vacancies' | 'candidates' | 'labels' | 'simple' | 'pairs'

export const ROLE_LABEL: Record<FileRole, string> = {
  vacancies: 'вакансии',
  candidates: 'кандидаты',
  labels: 'разметка',
  simple: 'набор целиком (вакансии, кандидаты, разметка)',
  pairs: 'строки набора пар (JSONL)',
}

type SimpleTables = { vacancies?: unknown; candidates?: unknown; labels?: unknown }

export interface PickedFile {
  name: string
  size: number
  role: FileRole | null
  /** CSV-текст таблицы, объект простого формата или строки набора пар */
  content: string | SimpleTables | Record<string, unknown>[] | null
  rows: number
  error: string | null
}

// Браузеру тяжело держать в памяти большие файлы; точный лимит загрузки — на бэкенде (config/demo.yaml).
const MAX_FILE_BYTES = 25 * 1024 * 1024

function headerOf(text: string): string[] {
  const first = text.replace(/^\uFEFF/, '').split(/\r?\n/, 1)[0] ?? ''
  const counts = [',', ';', '\t'].map((d) => [d, first.split(d).length - 1] as const)
  const [delim] = counts.reduce((a, b) => (b[1] > a[1] ? b : a))
  return first.split(delim).map((h) => h.trim().replace(/^"|"$/g, '').toLowerCase())
}

function csvRole(header: string[]): FileRole | null {
  if (header.includes('vacancy_id') && header.includes('candidate_id')) return 'labels'
  if (header.includes('text')) return 'vacancies'
  if (header.includes('spec') || header.includes('grade')) return 'candidates'
  return null
}

function dataLines(text: string): number {
  return Math.max(0, text.split(/\r?\n/).filter((l) => l.trim()).length - 1)
}

export async function readPicked(file: File): Promise<PickedFile> {
  const base = { name: file.name, size: file.size, role: null, content: null, rows: 0 }
  if (file.size > MAX_FILE_BYTES) {
    return { ...base, error: `Файл больше ${MAX_FILE_BYTES / 1024 / 1024} МБ: разделите набор на несколько загрузок.` }
  }
  const text = (await file.text()).replace(/^\uFEFF/, '')
  const lower = file.name.toLowerCase()
  if (!text.trim()) return { ...base, error: 'Файл пустой.' }

  if (lower.endsWith('.jsonl') || lower.endsWith('.ndjson')) {
    const rows: Record<string, unknown>[] = []
    const lines = text.split(/\r?\n/)
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].trim()) continue
      try {
        const v: unknown = JSON.parse(lines[i])
        if (!v || typeof v !== 'object' || Array.isArray(v)) return { ...base, error: `Строка ${i + 1}: ожидается JSON-объект.` }
        rows.push(v as Record<string, unknown>)
      } catch {
        return { ...base, error: `Строка ${i + 1}: это не JSON. В JSONL каждая строка — отдельный объект.` }
      }
    }
    return { ...base, role: 'pairs', content: rows, rows: rows.length, error: null }
  }

  if (lower.endsWith('.json')) {
    let v: unknown
    try {
      v = JSON.parse(text)
    } catch {
      return { ...base, error: 'Это не JSON. Сверьтесь с шаблоном example.json.' }
    }
    if (Array.isArray(v)) {
      const first = v[0] as Record<string, unknown> | undefined
      if (first && typeof first === 'object' && 'brief_id' in first && 'candidate_id' in first) {
        return { ...base, role: 'pairs', content: v as Record<string, unknown>[], rows: v.length, error: null }
      }
      return { ...base, error: 'Ожидается объект с ключами vacancies, candidates, labels (как в example.json) или массив строк набора пар.' }
    }
    if (v && typeof v === 'object' && ['vacancies', 'candidates', 'labels'].some((k) => k in v)) {
      const t = v as SimpleTables
      const rows = [t.vacancies, t.candidates, t.labels].reduce<number>((n, x) => n + (Array.isArray(x) ? x.length : 0), 0)
      return { ...base, role: 'simple', content: t, rows, error: null }
    }
    return { ...base, error: 'В JSON нет ключей vacancies, candidates или labels. Сверьтесь с шаблоном example.json.' }
  }

  const role = csvRole(headerOf(text))
  if (!role) {
    return {
      ...base,
      error:
        'Не понятно, какая это таблица. В первой строке нужен заголовок: у вакансий — id, text…, у кандидатов — id, spec, grade…, у разметки — vacancy_id, candidate_id, rel.',
    }
  }
  return { ...base, role, content: text, rows: dataLines(text), error: null }
}

export type UploadPlan =
  | { kind: 'pairs'; rows: Record<string, unknown>[] }
  | { kind: 'simple'; tables: { vacancies?: unknown; candidates?: unknown; labels?: unknown } }
  | { kind: 'error'; message: string }
  | { kind: 'empty' }

/** Что и куда отправить по набору выбранных файлов. */
export function planUpload(files: PickedFile[]): UploadPlan {
  if (files.length === 0) return { kind: 'empty' }
  const bad = files.find((f) => f.error)
  if (bad) return { kind: 'error', message: `${bad.name}: ${bad.error}` }
  const pairs = files.filter((f) => f.role === 'pairs')
  const tables = files.filter((f) => f.role !== 'pairs')
  if (pairs.length && tables.length) {
    return { kind: 'error', message: 'Смешаны форматы: загрузите либо строки набора пар (JSONL), либо таблицы простого формата.' }
  }
  if (pairs.length) return { kind: 'pairs', rows: pairs.flatMap((f) => f.content as Record<string, unknown>[]) }
  const out: { vacancies?: unknown; candidates?: unknown; labels?: unknown } = {}
  const from: Record<string, string> = {}
  for (const f of tables) {
    const parts: [keyof typeof out, unknown][] =
      f.role === 'simple'
        ? (['vacancies', 'candidates', 'labels'] as const)
            .filter((k) => (f.content as SimpleTables)[k] !== undefined)
            .map((k) => [k, (f.content as SimpleTables)[k]])
        : [[f.role as keyof typeof out, f.content]]
    for (const [k, v] of parts) {
      if (k in out) {
        return { kind: 'error', message: `Таблица «${ROLE_LABEL[k]}» есть и в «${from[k]}», и в «${f.name}»: оставьте одну.` }
      }
      out[k] = v
      from[k] = f.name
    }
  }
  if (out.vacancies === undefined && out.candidates === undefined) {
    return { kind: 'error', message: 'Нужны хотя бы вакансии или кандидаты: добавьте таблицу по шаблону.' }
  }
  return { kind: 'simple', tables: out }
}
