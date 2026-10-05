import { formatDecimal, formatInt } from '@/lib/format'

const NBSP = String.fromCharCode(0xa0)

/** Вилка для крупного показа: 220000–280000 → «220–280» + «тыс. ₽»; если не круглые тысячи — полные числа. */
export function salaryHero(from: number, to: number): { range: string; unit: string } {
  if (from % 500 === 0 && to % 500 === 0 && from >= 10_000) {
    return { range: `${formatDecimal(from / 1000)}–${formatDecimal(to / 1000)}`, unit: `тыс.${NBSP}₽` }
  }
  return { range: `${formatInt(from)}–${formatInt(to)}`, unit: '₽' }
}

/** «220–280 тыс. ₽ на руки» одной строкой. */
export function salaryLine(from: number, to: number, typeLabel?: string): string {
  const { range, unit } = salaryHero(from, to)
  return `${range}${NBSP}${unit}${typeLabel ? ` ${typeLabel}` : ''}`
}

/** Стаж в месяцах словами: 50 → «4 года 2 мес.» */
export function monthsLabel(months: number): string {
  const y = Math.floor(months / 12)
  const m = months % 12
  const yWord = y % 10 === 1 && y % 100 !== 11 ? 'год' : y % 10 >= 2 && y % 10 <= 4 && (y % 100 < 10 || y % 100 >= 20) ? 'года' : 'лет'
  if (y === 0) return `${m} мес.`
  return m === 0 ? `${y} ${yWord}` : `${y} ${yWord} ${m} мес.`
}

/** Размер файла: 1234567 → «1,2 МБ». */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${formatDecimal(bytes / 1024)} КБ`
  return `${formatDecimal(bytes / 1024 / 1024)} МБ`
}

/** Короткая дата «5 окт.» из ISO. */
export function shortDate(iso: string): string {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(new Date(iso))
}

/** Полная дата с временем «05.10.2026, 18:30». */
export function dateTime(iso: string): string {
  return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso),
  )
}

const CITIES: Record<string, string> = {
  moscow: 'Москва',
  spb: 'Санкт-Петербург',
  novosibirsk: 'Новосибирск',
  ekaterinburg: 'Екатеринбург',
  kazan: 'Казань',
  nizhny_novgorod: 'Нижний Новгород',
  chelyabinsk: 'Челябинск',
  samara: 'Самара',
  omsk: 'Омск',
  rostov: 'Ростов-на-Дону',
  ufa: 'Уфа',
  krasnoyarsk: 'Красноярск',
  voronezh: 'Воронеж',
}

/** Город из справочника приходит кодом («moscow») — показываем названием. Неизвестное оставляем как есть. */
export function cityLabel(city: string): string {
  return CITIES[city] ?? city
}
