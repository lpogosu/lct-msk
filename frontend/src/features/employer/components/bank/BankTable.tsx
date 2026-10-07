import { cn } from '@/lib/cn'
import { formatDecimal } from '@/lib/format'
import { Identicon, Ruler } from '@/ui'
import type { BankItem } from '../../api'
import { SEARCH_MODE_LABEL } from '../../dict'
import { Tag } from '../common'

function PinIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
      <path
        d="M8.6 1.6l3.8 3.8-1.7.7-1.5 2.7.3 1.6-.8.8-2.3-2.3-3.4 3.4-.7-.7 3.4-3.4L3.4 6.9l.8-.8 1.6.3 2.7-1.5.7-1.7z"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** Строки банка (Р9): как в подборке, но без Score брифа — балл банка 0,55·T + 0,25·F + 0,20·A. */
export function BankTable({
  items,
  selectedRef,
  onSelect,
  caption,
  hidden,
}: {
  items: BankItem[]
  selectedRef: string | null
  onSelect: (ref: string) => void
  caption: string
  hidden?: boolean
}) {
  return (
    <table className="w-full border-collapse text-left">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="border-b border-hairline t-small text-ink-3">
          <th scope="col" className="py-3 pl-3 pr-4 font-normal">
            Кандидат
          </th>
          <th scope="col" className="hidden w-[168px] py-3 pr-4 font-normal xl:table-cell">
            Уровень по тесту
          </th>
          <th scope="col" className="hidden py-3 pr-4 font-normal md:table-cell">
            Подтверждено
          </th>
          <th scope="col" className="w-[72px] py-3 pr-3 text-right font-normal">
            Балл
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const selected = it.ref === selectedRef
          const iv = it.test.interval
          const confirmed = it.skills.filter((s) => s.state === 'confirmed')
          const declared = it.skills.filter((s) => s.state === 'declared')
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
                'relative cursor-pointer border-b border-hairline align-middle transition-colors',
                selected ? 'bg-tint' : 'hover:bg-panel',
                hidden && 'opacity-70',
              )}
            >
              <td className="relative py-4 pl-3 pr-4">
                {selected && <span aria-hidden="true" className="absolute inset-y-0 left-0 w-0.5 bg-brand" />}
                <div className="flex items-center gap-3">
                  <Identicon seed={it.pseudonym} size={28} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="t-name text-ink">{it.pseudonym}</span>
                      {it.shortlisted && (
                        <span className="text-brand" title="В шортлисте брифа">
                          <PinIcon />
                          <span className="sr-only">в шортлисте</span>
                        </span>
                      )}
                      {it.invitation_label && <Tag tone="brand">{it.invitation_label}</Tag>}
                    </div>
                    <p className="t-small text-ink-2">{it.category}</p>
                    {!it.confirmed && (
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                        <Tag tone="warn">грейд не подтверждён</Tag>
                        <span className="t-small text-ink-3">категория заявлена, тестом не подтверждена</span>
                      </p>
                    )}
                    {it.search_mode && <p className="t-small text-ink-3">{SEARCH_MODE_LABEL[it.search_mode] ?? it.search_mode}</p>}
                  </div>
                </div>
              </td>
              <td className="hidden py-4 pr-4 xl:table-cell">
                {iv && iv.length >= 2 ? (
                  <div className="flex flex-col gap-0.5">
                    <Ruler interval={[iv[0], iv[1]]} size="compact" />
                    <span className="t-small text-ink-3">{it.test.band ?? `${iv[0]}–${iv[1]}`}</span>
                  </div>
                ) : (
                  <span className="t-small text-ink-3">тест не пройден</span>
                )}
              </td>
              <td className="hidden py-4 pr-4 md:table-cell">
                {it.modules.length > 0 && (
                  <p className="t-small text-ink">
                    {it.modules.map((m) => `${m.name} ${m.symbol}`).join(' · ')}
                  </p>
                )}
                {confirmed.length > 0 && (
                  <p className="t-small text-ink-2">Стек подтверждён: {confirmed.map((s) => s.name).join(', ')}</p>
                )}
                {declared.length > 0 && <p className="t-small text-ink-3">Заявлено: {declared.map((s) => s.name).join(', ')}</p>}
                <p className="line-clamp-1 t-small text-ink-3">{it.fsp_line}</p>
                {it.freshness && <p className="t-small text-ink-3">Свежесть: {it.freshness.label}</p>}
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
