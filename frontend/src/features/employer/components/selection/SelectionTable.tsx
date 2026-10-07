import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { formatDecimal } from '@/lib/format'
import { Identicon, Ruler } from '@/ui'
import type { SelectionItem } from '../../api'
import { Tag } from '../common'

const FLAG_LABEL: Record<string, string> = {
  borderline: 'тест на границе грейда',
  stale_test: 'тест устарел',
  unconfirmed: 'категория заявлена, тестом не подтверждена',
}

function PinIcon({ filled }: { filled?: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill={filled ? 'currentColor' : 'none'} aria-hidden="true" className="shrink-0">
      <path
        d="M8.6 1.6l3.8 3.8-1.7.7-1.5 2.7.3 1.6-.8.8-2.3-2.3-3.4 3.4-.7-.7 3.4-3.4L3.4 6.9l.8-.8 1.6.3 2.7-1.5.7-1.7z"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** Бейдж diff версии: «новый», «▲3», «▼2» (не «скрыт» — такие лежат в отдельной группе). */
function Badge({ item }: { item: SelectionItem }) {
  const b = item.badge
  if (!b || b.kind === 'same' || b.kind === 'hidden') return null
  return <Tag tone={b.kind === 'new' ? 'brand' : 'default'}>{b.label}</Tag>
}

/**
 * Турнирная таблица подборки (Figma 27:70): место, идентикон-псевдоним, категория, линейка теста,
 * главное «за» (и «против», если есть) и Score. Строка выбирается кликом или Enter — справа открывается панель кандидата.
 */
export function SelectionTable({
  items,
  selectedRef,
  onSelect,
  hidden,
  caption,
}: {
  items: SelectionItem[]
  selectedRef: string | null
  onSelect: (ref: string) => void
  /** Группа «Скрыто фильтрами»: без места, с причиной */
  hidden?: boolean
  caption: string
}) {
  return (
    <table className="w-full border-collapse text-left">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="border-b border-hairline t-small text-ink-3">
          <th scope="col" className="w-[64px] py-3 pl-3 font-normal sm:w-[72px]">
            {hidden ? '' : 'Место'}
          </th>
          <th scope="col" className="py-3 pr-4 font-normal">
            Кандидат
          </th>
          <th scope="col" className="hidden w-[168px] py-3 pr-4 font-normal xl:table-cell">
            Уровень по тесту
          </th>
          <th scope="col" className="hidden py-3 pr-4 font-normal md:table-cell">
            {hidden ? 'Почему скрыт' : 'Главное'}
          </th>
          <th scope="col" className="w-[72px] py-3 pr-3 text-right font-normal">
            Score
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const selected = it.ref === selectedRef
          const main = it.pros[0]?.text
          return (
            <tr
              key={it.ref}
              tabIndex={0}
              aria-selected={selected}
              onClick={() => onSelect(it.ref)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelect(it.ref)
                }
              }}
              className={cn(
                'group cursor-pointer border-b border-hairline align-middle transition-colors',
                selected ? 'bg-tint' : 'hover:bg-panel',
                hidden && 'opacity-70',
              )}
            >
              <td className="relative py-4 pl-3 align-middle">
                {selected && <span aria-hidden="true" className="absolute inset-y-0 left-0 w-0.5 bg-brand" />}
                <span className={cn('t-num-l', selected ? 'text-brand' : 'text-steel')}>{hidden ? '·' : (it.rank ?? '·')}</span>
              </td>
              <td className="py-4 pr-4">
                <div className="flex items-center gap-3">
                  <Identicon seed={it.pseudonym} size={28} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="t-name text-ink">{it.pseudonym}</span>
                      <Badge item={it} />
                      {!it.confirmed && <Tag tone="warn">грейд не подтверждён</Tag>}
                      {it.shortlisted && (
                        <span className="flex items-center gap-1 t-small text-brand" title="В шортлисте">
                          <PinIcon filled />
                          <span className="sr-only">в шортлисте</span>
                        </span>
                      )}
                      {it.invitation_label && <Tag tone="brand">{it.invitation_label}</Tag>}
                    </div>
                    <p className="t-small text-ink-2">{it.category}</p>
                    {it.weak_match && <p className="t-small text-ink-3">слабое соответствие брифу</p>}
                  </div>
                </div>
              </td>
              <td className="hidden py-4 pr-4 xl:table-cell">
                <RulerCell item={it} />
              </td>
              <td className="hidden py-4 pr-4 md:table-cell">
                {hidden ? (
                  <p className="t-small text-ink-2">{it.hidden_reasons.join('; ') || 'Не прошёл фильтры'}</p>
                ) : (
                  <>
                    <p className="line-clamp-3 t-small text-ink">{main ?? 'Данных для объяснения пока мало'}</p>
                    {it.con && (
                      <p className="mt-0.5 line-clamp-2 t-small text-danger-text">Против: {it.con.text}</p>
                    )}
                    {it.flags.length > 0 && (
                      <p className="mt-0.5 t-small text-ink-3">{it.flags.map((f) => FLAG_LABEL[f] ?? f).join(' · ')}</p>
                    )}
                  </>
                )}
              </td>
              <td className="py-4 pr-3 text-right">
                <span className="t-num-l text-ink">{formatDecimal(it.score, 0)}</span>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function RulerCell({ item }: { item: SelectionItem }): ReactNode {
  const iv = item.test.interval
  if (!iv || iv.length < 2) {
    return <p className="t-small text-ink-3">тест ещё не пройден</p>
  }
  return (
    <div className="flex flex-col gap-0.5">
      <Ruler interval={[iv[0], iv[1]]} size="compact" />
      <span className="t-small text-ink-3">{item.test.band ?? `${iv[0]}–${iv[1]}`}</span>
    </div>
  )
}
