/**
 * «Двойник в буфере» на клиенте — чистая логика без DOM (spec-antiassist §2.1, M2/M3).
 *
 * Сервер отдаёт в задании `clip = { render_id, clipboard_text }`: полный текст копии (формулировка, таблицы, код) с ОДНИМ
 * изменённым значением. Карты подмены в API нет — какой именно токен отличается, клиент находит сам, сравнивая
 * `composeShown(item)` (то, что видит кандидат) с `clipboard_text`. Это «сложный и редкий путь, сравнимый с
 * перепечаткой» (spec §7): текст всё равно лежит в буфере.
 *
 * Подмена с учётом выделения: если выделение не покрывает изменённый токен — копируется как есть; если покрывает —
 * токен в выделенном тексте заменяется на значение копии. Токен узнаётся по соседнему тексту (контекст до и после),
 * а не по позиции: выделение приходит из DOM, где пробелы и переводы строк могут отличаться от составного текста.
 * Вставка в собственное поле ответа (M3) выполняет обратную замену, чтобы честный кандидат не получил чужое значение.
 */

/** Данные двойника в задании: только то, что отдаёт API. */
export interface ClipPayload {
  render_id: string
  clipboard_text: string
}

/** То, что кандидат видит на экране: порядок и формат — как у `twins.compose_text` на бэкенде. */
export interface ShownItem {
  prompt?: string | null
  code?: string | null
  table?: unknown
}

export interface TwinPlan {
  /** значение на экране */
  from: string
  /** значение в копии */
  to: string
  /** текст перед токеном (нормализованные пробелы, до CONTEXT символов) */
  before: string
  /** текст после токена */
  after: string
}

/** Сколько символов окружения учитываем при узнавании токена */
export const CONTEXT = 12
/** Изменённый токен длиннее — считаем, что составные тексты разошлись, и подмену по токену не делаем */
const MAX_TOKEN = 40
/** Если токен найти не удалось: доля составного текста, начиная с которой выделение считается «всем заданием» */
export const WHOLE_SHARE = 0.6

interface TableLike {
  name?: unknown
  columns?: unknown
  rows?: unknown
  tables?: unknown
}

function cell(v: unknown): string {
  return v === null || v === undefined ? 'NULL' : String(v)
}

function tableBlock(t: TableLike): string | null {
  if (!Array.isArray(t.columns) || !Array.isArray(t.rows)) return null
  const head = [`Таблица ${String(t.name ?? '')}:`, t.columns.map(cell).join(' | ')]
  const rows = (t.rows as unknown[]).map((r) => (Array.isArray(r) ? r.map(cell).join(' | ') : cell(r)))
  return [...head, ...rows].join('\n')
}

/** Составной текст задания: формулировка, таблицы, код — через пустую строку (как на сервере). */
export function composeShown(item: ShownItem): string {
  const parts: string[] = [item.prompt ?? '']
  const t = item.table as TableLike | null | undefined
  if (t && typeof t === 'object') {
    const many = Array.isArray(t.tables) && t.tables.length > 0 ? (t.tables as TableLike[]) : [t]
    for (const x of many) {
      const b = tableBlock(x)
      if (b !== null) parts.push(b)
    }
  }
  if (item.code) parts.push(item.code)
  return parts.join('\n\n')
}

/**
 * JSON не отличает 4200.0 от 4200: у целочисленных значений «.0» убираем с обеих сторон сравнения. Версии и адреса
 * (127.0.0.1, 1.0.2) не трогаем: после «.0» у них идёт ещё точка или цифра.
 */
export function normalizeNumbers(text: string): string {
  return text.replace(/(?<![\d.])(\d+)\.0(?![\d.])/g, '$1')
}

const WORD = /[\p{L}\p{N}_]/u
const isWord = (ch: string | undefined): boolean => ch !== undefined && WORD.test(ch)

/** Находит единственный изменённый токен между показанным текстом и копией. null — тексты не различаются одним токеном. */
export function planTwin(shownRaw: string, clipRaw: string): TwinPlan | null {
  const shown = normalizeNumbers(shownRaw)
  const clip = normalizeNumbers(clipRaw)
  if (shown === clip) return null
  const min = Math.min(shown.length, clip.length)
  let p = 0
  while (p < min && shown[p] === clip[p]) p++
  let s = 0
  while (s < min - p && shown[shown.length - 1 - s] === clip[clip.length - 1 - s]) s++
  // Токен заменяется целиком: общие цифры/буквы с краёв («19» → «12») возвращаем в токен
  while (p > 0 && isWord(shown[p - 1])) p--
  while (s > 0 && isWord(shown[shown.length - s])) s--
  const from = shown.slice(p, shown.length - s)
  const to = clip.slice(p, clip.length - s)
  if (!from || !to || from === to || from.length > MAX_TOKEN || to.length > MAX_TOKEN) return null
  const before = shown.slice(Math.max(0, p - CONTEXT), p)
  const after = shown.slice(shown.length - s, Math.min(shown.length, shown.length - s + CONTEXT))
  return { from, to, before: collapse(before), after: collapse(after) }
}

function collapse(text: string): string {
  return text.replace(/\s+/g, ' ')
}

/** Нормализованный текст (пробелы схлопнуты) и соответствие позиций исходному. */
function normalizedWithMap(raw: string): { text: string; map: number[] } {
  let text = ''
  const map: number[] = []
  let inSpace = false
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i]
    if (/\s/.test(ch)) {
      if (!inSpace) {
        text += ' '
        map.push(i)
        inSpace = true
      }
    } else {
      text += ch
      map.push(i)
      inSpace = false
    }
  }
  return { text, map }
}

/**
 * Заменяет `find` на `repl` там, где окружение совпадает с контекстом плана. Окружение может быть оборвано границей
 * выделения: тогда сравниваем столько, сколько в нём есть. Возвращает исходный текст, если замен не было.
 */
function replaceByContext(raw: string, find: string, repl: string, before: string, after: string): { text: string; count: number } {
  const { text: norm, map } = normalizedWithMap(raw)
  const hits: number[] = []
  for (let i = norm.indexOf(find); i !== -1; i = norm.indexOf(find, i + 1)) {
    const left = norm.slice(Math.max(0, i - before.length), i)
    const right = norm.slice(i + find.length, i + find.length + after.length)
    const leftOk = before.endsWith(left) && (left.length === before.length || i - left.length === 0)
    const rightOk = after.startsWith(right) && (right.length === after.length || i + find.length + right.length === norm.length)
    if (leftOk && rightOk) hits.push(i)
  }
  if (hits.length === 0) return { text: raw, count: 0 }
  let out = ''
  let cursor = 0
  for (const i of hits) {
    const start = map[i]
    const end = map[i + find.length - 1] + 1
    out += raw.slice(cursor, start) + repl
    cursor = end
  }
  return { text: out + raw.slice(cursor), count: hits.length }
}

export interface Substitution {
  text: string
  /** выделение покрывало изменённый токен — подставлено значение копии */
  substituted: boolean
}

/** Копирование: токен экрана → токен копии в выделенном тексте. Выделение без токена остаётся как есть (M2). */
export function twinSelection(selection: string, plan: TwinPlan): Substitution {
  const r = replaceByContext(selection, plan.from, plan.to, plan.before, plan.after)
  return { text: r.text, substituted: r.count > 0 }
}

/** Вставка в собственное поле (M3): токен копии → токен экрана. */
export function restoreOnPaste(pasted: string, plan: TwinPlan): Substitution {
  const r = replaceByContext(pasted, plan.to, plan.from, plan.before, plan.after)
  return { text: r.text, substituted: r.count > 0 }
}

/** Запасной вариант, когда токен найти не удалось: выделено почти всё задание — кладём копию целиком. */
export function wholeTaskSelected(selection: string, shown: string): boolean {
  const need = shown.replace(/\s+/g, ' ').trim().length * WHOLE_SHARE
  return selection.replace(/\s+/g, ' ').trim().length >= need && need > 0
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
