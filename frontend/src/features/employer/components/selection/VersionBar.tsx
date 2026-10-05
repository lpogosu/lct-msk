import { useState } from 'react'
import { Button, Panel, Trace, type TraceNode } from '@/ui'
import { formatInt, plural } from '@/lib/format'
import type { VersionMeta, VersionView } from '../../api'

function filtersText(v: VersionMeta): string {
  if (v.filters.length === 0) return 'без фильтров'
  return v.filters
    .map((f) => {
      const val = Array.isArray(f.value) ? f.value.length : f.value
      return f.code === 'min_score' && val != null ? `${f.label} ${val}` : f.label
    })
    .join(', ')
    .replace(/^./, (c) => c.toLowerCase())
}

/**
 * Цепочка версий подборки (Figma 27:37): любой фильтр или правка брифа — новая неизменяемая версия.
 * Версию можно открыть для просмотра или откатиться к ней (последние 10).
 */
export function VersionBar({
  view,
  versions,
  rollbackTargets,
  currentN,
  onOpen,
  onRollback,
  busy,
}: {
  view: VersionView
  versions: VersionMeta[]
  rollbackTargets: number[]
  currentN: number | null
  onOpen: (n: number | null) => void
  onRollback: (n: number) => void
  busy: boolean
}) {
  const [open, setOpen] = useState(false)
  const viewingOld = currentN != null && view.n !== currentN
  const tail = versions.slice(-6)
  const nodes: TraceNode[] = tail.map((v, i) => ({
    at: tail.length === 1 ? 1 : i / (tail.length - 1),
    variant: v.n === view.n ? 'current' : i === 0 ? 'open' : 'solid',
  }))
  const newCount = view.items.filter((i) => i.badge?.kind === 'new').length
  const parent = view.parent_n

  return (
    <div className="flex flex-col gap-3">
      {viewingOld && (
        <Panel variant="tint" radius="md" padding="sm" className="flex flex-wrap items-center justify-between gap-3">
          <p className="t-body text-ink">
            Вы смотрите версию v{view.n}, текущая — v{currentN}. Эта подборка не меняется, пока вы не откатитесь к ней.
          </p>
          <div className="flex flex-wrap gap-3">
            {rollbackTargets.includes(view.n) && (
              <Button size="sm" loading={busy} onClick={() => onRollback(view.n)}>
                Откатиться к v{view.n}
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => onOpen(null)}>
              К текущей v{currentN}
            </Button>
          </div>
        </Panel>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="w-[88px] shrink-0">
          <Trace nodes={nodes.length > 0 ? nodes : [{ at: 0, variant: 'open' }, { at: 1, variant: 'current' }]} height={14} inset={6} label={`Версии подборки: ${versions.map((v) => `v${v.n}`).join(', ')}`} />
        </div>
        <p className="min-w-0 flex-1 t-body text-ink-2" aria-live="polite">
          Версия {view.n}: {filtersText(view)}.{' '}
          {parent != null && newCount > 0 && (
            <>
              {formatInt(newCount)} {plural(newCount, ['новый', 'новых', 'новых'])}
              {view.pages > 1 ? ' на странице' : ''},{' '}
            </>
          )}
          {view.hidden_n > 0 ? `Скрыто: ${formatInt(view.hidden_n)}` : 'Ничего не скрыто'}.
        </p>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          {!viewingOld && parent != null && rollbackTargets.includes(parent) && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onRollback(parent)}
              className="t-label text-brand underline-offset-4 hover:underline disabled:text-ink-3"
            >
              Вернуть v{parent}
            </button>
          )}
          {versions.length > 1 && (
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="t-label text-brand underline-offset-4 hover:underline"
            >
              {open ? 'Скрыть версии' : `Все версии (${versions.length})`}
            </button>
          )}
        </div>
      </div>

      {open && (
        <ol className="m-0 flex list-none flex-col divide-y divide-hairline rounded-panel border border-hairline p-0">
          {[...versions].reverse().map((v) => (
            <li key={v.n} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
              <div className="min-w-0">
                <p className="t-label text-ink">
                  v{v.n}
                  {v.n === currentN && <span className="ml-2 t-small text-brand">текущая</span>}
                  {v.n === view.n && v.n !== currentN && <span className="ml-2 t-small text-ink-3">открыта</span>}
                </p>
                <p className="t-small text-ink-3">
                  {filtersText(v)} · видно {formatInt(v.visible_n)}, скрыто {formatInt(v.hidden_n)}
                </p>
              </div>
              <div className="flex gap-4">
                {v.n !== view.n && (
                  <button type="button" onClick={() => onOpen(v.n === currentN ? null : v.n)} className="t-label text-brand underline-offset-4 hover:underline">
                    Открыть
                  </button>
                )}
                {v.n !== currentN && rollbackTargets.includes(v.n) && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onRollback(v.n)}
                    className="t-label text-brand underline-offset-4 hover:underline disabled:text-ink-3"
                  >
                    Откатиться
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
