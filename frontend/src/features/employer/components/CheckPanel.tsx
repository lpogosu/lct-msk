import { Link } from 'react-router-dom'
import { formatDecimal } from '@/lib/format'
import { Skeleton, TrustNode } from '@/ui'
import type { CheckOut, ParseOut } from '../api'
import { MODULE_LABEL } from '../brief'
import { BlockTitle } from './common'

interface Row {
  key: string
  skill: string
  text: string
  tested: boolean
}

/** «PostgreSQL ✓ проверено стандартным тестом категории (…)» → часть после названия навыка и значка. */
function detailOf(name: string, text: string): string {
  return text.startsWith(name) ? text.slice(name.length).replace(/^\s*[✓✗]\s*/, '') : text
}

/**
 * Навык сопоставлен модулю теста, но «проверяется» он не всегда: бэкенд говорит по специализации брифа (`via`):
 * стандартным тестом категории, проверочным набором по вакансии или только на интервью (модуля нет в тесте категории).
 */
function rowsFromCheck(check: CheckOut): Row[] {
  return [
    ...check.covered.map((c) => ({
      key: c.skill_id,
      skill: c.name,
      tested: c.via !== 'interview',
      text: detailOf(c.name, c.text),
    })),
    ...check.untestable.map((u) => ({
      key: u.skill_id,
      skill: u.name,
      tested: false,
      text: 'тестом не проверяется — спросите на интервью',
    })),
  ]
}

function rowsFromParse(parse: ParseOut): Row[] {
  return parse.skills.map((s) => {
    const modules = s.modules ?? []
    const tested = !s.untestable && modules.length > 0
    return {
      key: s.skill_id,
      skill: s.name,
      tested,
      text: tested
        ? `${modules.length > 1 ? 'модули' : 'модуль'} ${modules.map((m) => `«${MODULE_LABEL[m] ?? m}»`).join(', ')}`
        : 'тестом не проверяется — спросите на интервью',
    }
  })
}

/**
 * «Как мы проверим» (Figma 40:2, Р4): какие навыки брифа закрывает стандартный тест категории,
 * а какие в тест не попадают и остаются на интервью. До сохранения брифа список строится из разбора,
 * после — из GET /needs/{id}/check вместе с планом проверочного набора.
 */
export function CheckPanel({ parse, check, loading }: { parse: ParseOut | null; check?: CheckOut; loading?: boolean }) {
  const rows = check ? rowsFromCheck(check) : parse ? rowsFromParse(parse) : []
  return (
    <section aria-labelledby="check-title" className="flex flex-col gap-3">
      <BlockTitle>
        <span id="check-title">Как мы проверим</span>
      </BlockTitle>
      {loading && rows.length === 0 ? (
        <Skeleton className="h-16 w-full" />
      ) : rows.length === 0 ? (
        <p className="t-body text-ink-3">Появится, когда в тексте найдутся навыки.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {rows.map((r) => (
            <li key={r.key} className="grid grid-cols-[18px_minmax(0,120px)_minmax(0,1fr)] items-start gap-x-3 t-body sm:grid-cols-[18px_minmax(0,140px)_minmax(0,1fr)]">
              <span className="mt-[3px]">
                <TrustNode kind={r.tested ? 'test' : 'claimed'} size={18} surface="#F6F6F8" label={r.tested ? 'проверяется тестом' : 'тестом не проверяется'} />
              </span>
              <span className="text-ink">{r.skill}</span>
              <span className={r.tested ? 'text-ink-2' : 'text-ink-3'}>{r.text}</span>
            </li>
          ))}
        </ul>
      )}
      {check && check.check_set.slots.length > 0 && (
        <p className="t-small text-ink-3">
          Проверочный набор по вакансии: {check.check_set.slots.length} задач, около{' '}
          {formatDecimal(check.check_set.total_minutes, 1)} мин. Каждому кандидату — свои одноразовые экземпляры.
        </p>
      )}
      {check?.untestable.length ? (
        <p className="t-small text-ink-3">
          Хотите проверить такие навыки до интервью?{' '}
          <Link to="/employer/tasks" className="text-brand underline-offset-4 hover:underline">
            Дайте кандидатам свою задачу
          </Link>
          .
        </p>
      ) : null}
    </section>
  )
}
