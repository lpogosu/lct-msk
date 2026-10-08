import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { cn } from '@/lib/cn'
import { formatDate, formatInt } from '@/lib/format'

/**
 * Подсказка «рыночная вилка» в форме брифа (открытые данные вакансий, backend/app/matching/market.py).
 * Только ориентир: в Score не входит и вилку не блокирует. Основание (на руки / до НДФЛ) подписано всегда —
 * показываем то, что выбрал работодатель, чтобы цифры можно было сравнивать с его вилкой.
 */
export function MarketHint({
  spec,
  grade,
  salaryTo,
  net,
  className,
}: {
  spec: string | null | undefined
  grade: string | null | undefined
  salaryTo: number | null | undefined
  net: boolean
  className?: string
}) {
  const g = grade === 'intern' || grade === 'junior' || grade === 'middle' || grade === 'senior' ? grade : undefined
  const q = useQuery({
    queryKey: ['employer', 'market-salary', spec, g, salaryTo ?? null, net],
    enabled: !!spec,
    staleTime: 10 * 60_000,
    queryFn: () =>
      unwrap(
        api.GET('/api/needs/market-salary', {
          params: { query: { specialization: spec as string, grade: g, salary_to: salaryTo ?? undefined, salary_net: net } },
        }),
      ),
  })
  const d = q.data
  if (!d || !d.available || !d.gross || !d.net) return null
  const s = net ? d.net : d.gross
  const basis = net ? 'на руки' : 'до НДФЛ'
  const k = (v: number) => formatInt(Math.round(v / 1000))
  return (
    <aside
      className={cn('flex flex-col gap-1 rounded-lg border border-steel bg-panel px-4 py-3', className)}
      aria-label="Рыночная вилка"
    >
      <p className="m-0 t-small text-ink-2">
        <span className="t-body-strong text-ink">Рыночная вилка: {k(s.p25)}–{k(s.p75)}&nbsp;тыс.&nbsp;₽ {basis}</span>
        <span className="text-ink-3"> · медиана {k(s.p50)}&nbsp;тыс. · по {d.n}&nbsp;вакансиям</span>
      </p>
      {d.far_below && (
        <p className="m-0 t-small text-[#7A5B00]">
          Ваша вилка заметно ниже рынка: кандидаты могут отказываться. Это подсказка, сохранить бриф можно.
        </p>
      )}
      <p className="m-0 t-small text-ink-3">
        {d.scope === 'direction' ? 'Направление целиком (по грейду мало данных). ' : ''}
        Открытые вакансии «Работа в России»{d.fetched ? `, ${formatDate(d.fetched)}` : ''}. {d.note}
      </p>
    </aside>
  )
}
