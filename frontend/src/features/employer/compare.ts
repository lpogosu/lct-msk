import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CandidateCard, SelectionItem } from './api'

// «Сравнить кандидатов» в подборке брифа: выбор живёт отдельно от версий (как шортлист) — переживает фильтры,
// смену страницы и уточнение брифа. Хранится в sessionStorage вкладки по id брифа: это удобство одного
// работодателя, а не общее состояние, поэтому ни сервер, ни другие вкладки о нём не знают.

/** Больше трёх колонок перестают читаться бок о бок (а на телефоне это уже четыре экрана свайпа). */
export const COMPARE_MAX = 3

export interface CompareEntry {
  ref: string
  /** Псевдоним «К-4821» — чтобы плавающая панель могла подписать кандидата, которого нет на текущей странице */
  pseudonym: string
}

export type CompareToggleResult = 'added' | 'removed' | 'full'

const storageKey = (needId: string) => `vz.compare.${needId}`

function isEntry(v: unknown): v is CompareEntry {
  return !!v && typeof (v as CompareEntry).ref === 'string' && typeof (v as CompareEntry).pseudonym === 'string'
}

function readStored(needId: string): CompareEntry[] {
  try {
    const raw = window.sessionStorage.getItem(storageKey(needId))
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter(isEntry).slice(0, COMPARE_MAX) : []
  } catch {
    return [] // приватный режим или заблокированное хранилище: начинаем с пустого выбора
  }
}

function writeStored(needId: string, entries: CompareEntry[]): void {
  try {
    if (entries.length > 0) window.sessionStorage.setItem(storageKey(needId), JSON.stringify(entries))
    else window.sessionStorage.removeItem(storageKey(needId))
  } catch {
    // хранилище недоступно — выбор просто не переживёт перезагрузку
  }
}

/** Выбор «к сравнению» для брифа: до COMPARE_MAX кандидатов, порядок — порядок отметки. */
export function useCompareSet(needId: string) {
  const [entries, setEntries] = useState<CompareEntry[]>(() => readStored(needId))

  useEffect(() => writeStored(needId, entries), [needId, entries])

  const refs = useMemo(() => new Set(entries.map((e) => e.ref)), [entries])

  const toggle = useCallback(
    (entry: CompareEntry): CompareToggleResult => {
      if (refs.has(entry.ref)) {
        setEntries((cur) => cur.filter((e) => e.ref !== entry.ref))
        return 'removed'
      }
      if (entries.length >= COMPARE_MAX) return 'full'
      setEntries((cur) => (cur.some((e) => e.ref === entry.ref) ? cur : [...cur, entry].slice(0, COMPARE_MAX)))
      return 'added'
    },
    [entries.length, refs],
  )

  const remove = useCallback((ref: string) => setEntries((cur) => cur.filter((e) => e.ref !== ref)), [])
  const clear = useCallback(() => setEntries([]), [])

  return { entries, refs, toggle, remove, clear, full: entries.length >= COMPARE_MAX }
}

// --------------------------------------------------------------------------- колонка сравнения (только CandidateBlind)

/**
 * Колонка экрана сравнения. Собирается из строки подборки и слепой карточки по белому списку полей: блок `disclosed`
 * (имя, контакты, вуз, работодатель, резюме) сюда не попадает даже после взаимного «да» — сравнение строится на
 * доказательствах, а не на личности (CLAUDE.md: CandidateBlind vs CandidateDisclosed).
 */
export interface CompareColumn {
  ref: string
  pseudonym: string
  state: 'loading' | 'ready' | 'unavailable' | 'error'
  /** Пояснение, если кандидат больше не виден в подборе */
  note: string | null
  /** Строка подборки: разложение Score, «за/против», чипы навыков брифа, ФСП обобщённо, свежесть, сигнал по зарплате */
  item: SelectionItem | null
  categoryName: string
  confirmed: boolean | null
  testDate: string | null
  formats: string[]
  region: string | null
  interest: CandidateCard['interest'] | null
}

export function blindColumn(
  entry: CompareEntry,
  card: CandidateCard | undefined,
  rowItem: SelectionItem | undefined,
  q: { isPending: boolean; isError: boolean },
): CompareColumn {
  // Как в панели доказательств: сначала строка текущей версии из карточки, иначе — строка уже загруженной страницы
  const item = card?.selection ?? rowItem ?? null
  const unavailable = card ? !card.available : false
  return {
    ref: entry.ref,
    pseudonym: card?.pseudonym ?? item?.pseudonym ?? entry.pseudonym,
    state: unavailable ? 'unavailable' : q.isError && !item ? 'error' : q.isPending && !item ? 'loading' : 'ready',
    note: unavailable ? (card?.note ?? 'Кандидат сейчас не виден в подборе: он изменил настройки или ушёл в «не ищу».') : null,
    item: unavailable ? null : item,
    categoryName: card?.category?.name ?? item?.category ?? '',
    confirmed: item?.confirmed ?? card?.category?.confirmed ?? null,
    testDate: card?.test_date ?? null,
    formats: card?.formats ?? [],
    region: card?.region ?? null,
    interest: card?.interest ?? item?.interest ?? null,
  }
}

// --------------------------------------------------------------------------- подсветка лучшего в строке

/**
 * Индексы колонок с лучшим значением строки. Подсвечиваем, только когда есть с чем сравнить (≥ 2 значений)
 * и значения различаются больше чем на шаг округления: одинаковым «лучшего» нет, и общего победителя тоже нет.
 */
export function bestOf(values: readonly (number | null | undefined)[], dir: 'max' | 'min' = 'max', eps = 0.05): Set<number> {
  const present = values.map((v, i) => [v, i] as const).filter((p): p is readonly [number, number] => p[0] != null)
  if (present.length < 2) return new Set()
  const nums = present.map(([v]) => v)
  const best = dir === 'max' ? Math.max(...nums) : Math.min(...nums)
  const worst = dir === 'max' ? Math.min(...nums) : Math.max(...nums)
  if (Math.abs(best - worst) <= eps) return new Set()
  return new Set(present.filter(([v]) => Math.abs(v - best) <= eps).map(([, i]) => i))
}

/** Σ вкладов с шагом 0,1 — та же арифметика, что у бэкенда (display_parts: вклады в сумме ровно дают Score). */
export function sumParts(parts: Record<string, number>, keys: readonly string[]): number {
  return Math.round(keys.reduce((s, k) => s + (parts[k] ?? 0) * 10, 0)) / 10
}
