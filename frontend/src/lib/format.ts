// Форматирование чисел и дат для интерфейса (русская локаль). Все цифры показываем так же, как в Figma:
// тысячи — через неразрывный пробел, десятичная запятая.

const NBSP = String.fromCharCode(0xa0) // неразрывный пробел
const NNBSP = String.fromCharCode(0x202f) // узкий неразрывный пробел (так Intl разделяет тысячи)

/** 1214 → «1 214» */
export function formatInt(n: number): string {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(n).split(NNBSP).join(NBSP)
}

/** 36.4 → «36,4»; 20 → «20» */
export function formatDecimal(n: number, maxFractionDigits = 1): string {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: maxFractionDigits }).format(n)
}

/** 220000 → «220 000 ₽» */
export function formatRub(n: number): string {
  return `${formatInt(n)}${NBSP}₽`
}

/** Вилка в тысячах: (220000, 280000) → «220–280 тыс. ₽» */
export function formatRubRangeK(from: number, to: number): string {
  return `${formatDecimal(from / 1000)}–${formatDecimal(to / 1000)}${NBSP}тыс.${NBSP}₽`
}

/** Склонение: plural(3, ['кандидат', 'кандидата', 'кандидатов']) → «кандидата» */
export function plural(n: number, forms: readonly [string, string, string]): string {
  const k = Math.abs(n) % 100
  const last = k % 10
  if (k > 10 && k < 20) return forms[2]
  if (last > 1 && last < 5) return forms[1]
  if (last === 1) return forms[0]
  return forms[2]
}

/** «3 кандидата» */
export function countWord(n: number, forms: readonly [string, string, string]): string {
  return `${formatInt(n)}${NBSP}${plural(n, forms)}`
}

/** «3 октября» */
export function formatDayMonth(value: string | number | Date): string {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(new Date(value))
}

/** «05.10.2026» */
export function formatDate(value: string | number | Date): string {
  return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(
    new Date(value),
  )
}

/** Секунды → «01:12» (таймер теста) */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}
