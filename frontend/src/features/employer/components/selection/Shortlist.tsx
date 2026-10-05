import { formatDecimal } from '@/lib/format'
import { Button, Identicon } from '@/ui'
import type { ShortlistEntry } from '../../api'
import { BlockTitle, Tag } from '../common'

/**
 * Шортлист 📌 (живёт отдельно от версий): кандидат, не прошедший фильтры текущей версии или выпавший из неё,
 * остаётся в списке с жёлтой меткой и причиной (концепция §3.2, шаг 6).
 */
export function Shortlist({
  entries,
  limit,
  selectedRef,
  onOpen,
  onRemove,
  busyRef,
  onCheckSet,
}: {
  entries: ShortlistEntry[]
  limit: number
  selectedRef: string | null
  onOpen: (ref: string) => void
  onRemove: (ref: string) => void
  busyRef: string | null
  /** Проверочный набор по вакансии для кандидатов шортлиста */
  onCheckSet?: () => void
}) {
  return (
    <section aria-labelledby="shortlist-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <BlockTitle>
          <span id="shortlist-title">
            Шортлист · {entries.length} из {limit}
          </span>
        </BlockTitle>
        {onCheckSet && entries.length > 0 && (
          <Button variant="text" onClick={onCheckSet}>
            Проверочный набор…
          </Button>
        )}
      </div>
      {entries.length === 0 ? (
        <p className="t-body text-ink-3">
          Закрепляйте кандидатов кнопкой «В шортлист» в карточке справа: так их можно пригласить пачкой — до 10 за раз.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-hairline border-y border-hairline p-0">
          {entries.map((e) => (
            <li key={e.ref} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
              <Identicon seed={e.pseudonym} size={28} />
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 t-label text-ink">
                  {e.pseudonym}
                  {e.item && <span className="font-normal text-ink-3">{e.item.category} · Score {formatDecimal(e.item.score, 0)}</span>}
                  {e.mark === 'yellow' && <Tag tone="warn">не в текущей версии</Tag>}
                  {e.item?.invitation_label && <Tag tone="brand">{e.item.invitation_label}</Tag>}
                </p>
                {e.reason && <p className="t-small text-ink-3">{e.reason}</p>}
                {e.note && <p className="t-small text-ink-2">Заметка: {e.note}</p>}
              </div>
              <div className="flex items-center gap-4">
                <Button variant="text" onClick={() => onOpen(e.ref)} aria-pressed={selectedRef === e.ref}>
                  Открыть
                </Button>
                <Button variant="text" loading={busyRef === e.ref} onClick={() => onRemove(e.ref)}>
                  Убрать
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
