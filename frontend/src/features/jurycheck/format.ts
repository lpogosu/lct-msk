import { formatDecimal, formatRubRangeK, withSalaryBasis } from '@/lib/format'
import type { MetricValue } from './api'

// Подписи и числа страницы проверки подбора (/lab/check).

/** 0.61 → «0,61»; нет значения — «—». */
export function fmtMetric(v: number | null | undefined): string {
  return v == null ? '—' : formatDecimal(v, 2)
}

/** 95% ДИ: «0,48–0,72»; не посчитан (брифов меньше двух) — пусто. */
export function fmtCi(m: MetricValue | undefined): string {
  if (!m || m.ci_low == null || m.ci_high == null) return ''
  return `${fmtMetric(m.ci_low)}–${fmtMetric(m.ci_high)}`
}

export function metricKeys(ks: number[], ndcgK: number): { key: string; label: string }[] {
  return [...ks.map((k) => ({ key: `p@${k}`, label: `P@${k}` })), { key: `ndcg@${ndcgK}`, label: `nDCG@${ndcgK}` }]
}

export function salaryText(from: number | null | undefined, to: number | null | undefined, net: boolean | null | undefined): string {
  if (from == null || to == null) return '—'
  return withSalaryBasis(formatRubRangeK(from, to), net)
}

const FORMAT_RU: Record<string, string> = { office: 'офис', hybrid: 'гибрид', remote: 'удалённо' }

export function formatsText(formats: string[] | null | undefined): string {
  return formats && formats.length ? formats.map((f) => FORMAT_RU[f] ?? f).join(', ') : '—'
}

export const FLAG_LABEL: Record<string, string> = {
  borderline: 'тест на границе грейда',
  stale_test: 'тест устарел',
  low_trust: 'есть мягкие флаги теста',
  unconfirmed: 'категория по опросу, тестом не подтверждена',
}
