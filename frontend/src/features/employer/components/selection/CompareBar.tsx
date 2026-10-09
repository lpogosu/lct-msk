import { cn } from '@/lib/cn'
import { Button, Identicon } from '@/ui'
import { COMPARE_MAX, type CompareEntry } from '../../compare'

function Cross() {
  return (
    <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

/**
 * Отметка «к сравнению» в строке таблицы подборки: квадрат-флажок. Клик и пробел не открывают карточку —
 * строка таблицы сама кликабельная, поэтому событие дальше ячейки не всплывает.
 */
export function CompareCheck({ checked, pseudonym, onToggle }: { checked: boolean; pseudonym: string; onToggle: () => void }) {
  return (
    <label
      className="-m-2 inline-flex cursor-pointer items-center justify-center p-2"
      title={checked ? 'Убрать из сравнения' : 'Отметить к сравнению'}
      onClick={(e) => e.stopPropagation()}
    >
      <input
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        onChange={onToggle}
        onKeyDown={(e) => e.stopPropagation()}
        aria-label={`К сравнению: ${pseudonym}`}
        data-testid="compare-check"
      />
      <span
        aria-hidden="true"
        className={cn(
          'flex h-[18px] w-[18px] items-center justify-center rounded-[5px] border border-steel bg-white transition-colors',
          'peer-checked:border-brand peer-checked:bg-brand peer-hover:border-brand',
          'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brand',
          '[&>svg]:opacity-0 peer-checked:[&>svg]:opacity-100',
        )}
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M3.5 7.2L6 9.5L10.5 4.5" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    </label>
  )
}

/**
 * Плавающая панель внизу экрана подборки: кто отмечен к сравнению (до 3) и кнопка «Сравнить (N)».
 * Видна, пока выбран хотя бы один кандидат; выбор переживает фильтры, страницы и уточнение брифа.
 */
export function CompareBar({
  entries,
  onRemove,
  onClear,
  onOpen,
}: {
  entries: CompareEntry[]
  onRemove: (ref: string) => void
  onClear: () => void
  onOpen: () => void
}) {
  if (entries.length === 0) return null
  const n = entries.length
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-3 pb-3 sm:pb-5">
      <section
        aria-label="Кандидаты к сравнению"
        className="pointer-events-auto flex w-full max-w-[780px] flex-wrap items-center gap-x-4 gap-y-2 rounded-panel-lg border border-hairline bg-white px-4 py-3 shadow-float sm:flex-nowrap sm:px-5"
      >
        <p className="shrink-0 t-small text-ink-3" aria-live="polite">
          К сравнению · {n} из {COMPARE_MAX}
        </p>
        <ul className="m-0 flex min-w-0 flex-1 list-none gap-2 overflow-x-auto p-0">
          {entries.map((e) => (
            <li key={e.ref} className="flex shrink-0 items-center gap-1.5 rounded-pill bg-panel py-1 pl-1.5 pr-1">
              <Identicon seed={e.pseudonym} size={20} />
              <span className="t-label text-ink">{e.pseudonym}</span>
              <button
                type="button"
                onClick={() => onRemove(e.ref)}
                aria-label={`Убрать ${e.pseudonym} из сравнения`}
                className="flex h-6 w-6 items-center justify-center rounded-full text-ink-3 hover:bg-white hover:text-ink"
              >
                <Cross />
              </button>
            </li>
          ))}
        </ul>
        <div className="flex shrink-0 items-center gap-4">
          <Button variant="text" onClick={onClear}>
            Очистить
          </Button>
          <Button size="sm" disabled={n < 2} onClick={onOpen} title={n < 2 ? 'Отметьте ещё хотя бы одного кандидата' : undefined}>
            {n < 2 ? 'Сравнить' : `Сравнить (${n})`}
          </Button>
        </div>
      </section>
    </div>
  )
}
