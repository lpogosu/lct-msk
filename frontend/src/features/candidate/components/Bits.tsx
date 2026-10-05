import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { TrustNode, type NodeKind } from '@/ui'

/** Заголовок раздела внутри экрана (Figma K5: «Насколько мы уверены», «Модули теста»). */
export function SectionTitle({
  children,
  right,
  className,
  as: Tag = 'h2',
}: {
  children: ReactNode
  right?: ReactNode
  className?: string
  as?: 'h2' | 'h3'
}) {
  return (
    <div className={cn('mb-3 flex items-baseline justify-between gap-3', className)}>
      <Tag className="t-name text-ink">{children}</Tag>
      {right && <div className="shrink-0 t-small text-ink-3">{right}</div>}
    </div>
  )
}

/** Заголовок экрана: моно-фиолетовый title (как «Middle» в К5) и необязательная надстрочная подпись. */
export function ScreenTitle({ eyebrow, children, lead }: { eyebrow?: ReactNode; children: ReactNode; lead?: ReactNode }) {
  return (
    <div className="mb-6">
      {eyebrow && <p className="mb-1 t-body text-ink-3">{eyebrow}</p>}
      <h1 className="t-title text-brand">{children}</h1>
      {lead && <p className="mt-2 t-lead text-ink">{lead}</p>}
    </div>
  )
}

type ChipTone = 'tint' | 'panel' | 'outline' | 'danger' | 'brand'

const CHIP_TONE: Record<ChipTone, string> = {
  tint: 'bg-tint text-brand',
  panel: 'bg-panel text-ink-2',
  outline: 'border border-hairline bg-white text-ink-2',
  danger: 'bg-[#FFF1F3] text-danger-text',
  brand: 'bg-brand text-white',
}

export function Chip({ tone = 'tint', children, className }: { tone?: ChipTone; children: ReactNode; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-pill px-2.5 py-0.5 t-small font-medium', CHIP_TONE[tone], className)}>
      {children}
    </span>
  )
}

/** Чип навыка с узлом доверия: подтверждено тестом / ФСП или серое «заявлено». */
export function SkillChip({ name, kind }: { name: string; kind: NodeKind }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 t-small',
        kind === 'claimed' ? 'border border-dashed border-steel bg-white text-ink-3' : 'bg-panel text-ink',
      )}
    >
      <TrustNode kind={kind} surface={kind === 'claimed' ? '#FFFFFF' : '#F6F6F8'} />
      {name}
    </span>
  )
}

const chevron = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="shrink-0 text-ink-3">
    <path d="M6 3.5L10.5 8 6 12.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

/** Строка-ссылка списка разделов: заголовок, пояснение и шеврон; между строками — hairline. */
export function RowLink({
  to,
  title,
  detail,
  badge,
}: {
  to: string
  title: ReactNode
  detail?: ReactNode
  badge?: ReactNode
}) {
  return (
    <Link
      to={to}
      className="-mx-2 flex min-h-14 items-center gap-3 rounded-panel px-2 py-3 text-ink no-underline hover:bg-panel focus-visible:bg-panel"
    >
      <span className="min-w-0 flex-1">
        <span className="block t-label text-ink">{title}</span>
        {detail && <span className="mt-0.5 block t-small text-ink-3">{detail}</span>}
      </span>
      {badge}
      {chevron}
    </Link>
  )
}

/** Подпись «ключ — значение» в карточках (Email · Регистрация…). */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="t-small text-ink-3">{label}</p>
      <div className="t-label text-ink">{children}</div>
    </div>
  )
}

/** Список с узлами-маркерами трассы (Figma «Почему позвали вас», правила теста). */
export function TraceList({ items, className }: { items: ReactNode[]; className?: string }) {
  return (
    <ul className={cn('flex flex-col gap-2.5', className)}>
      {items.map((it, i) => (
        <li key={i} className="flex gap-3 t-body text-ink">
          <span aria-hidden="true" className="mt-[7px] h-2 w-2 shrink-0 rounded-full bg-brand" />
          <span className="min-w-0">{it}</span>
        </li>
      ))}
    </ul>
  )
}

/** Горизонтальная вероятностная полоса: «Junior 3% · Middle 66% · Senior 31%» (Figma K5, «Насколько мы уверены»). */
export function ProbabilityBar({
  parts,
  label,
}: {
  parts: { name: string; percent: number; active?: boolean }[]
  label: string
}) {
  const total = parts.reduce((s, p) => s + p.percent, 0) || 1
  return (
    <div>
      <div role="img" aria-label={label} className="flex h-3 w-full gap-[2px] overflow-hidden rounded-pill">
        {parts.map((p) =>
          p.percent > 0 ? (
            <span
              key={p.name}
              className={cn('block h-full', p.active ? 'bg-brand' : 'bg-heat-3')}
              style={{ width: `${(p.percent / total) * 100}%`, minWidth: 6 }}
            />
          ) : null,
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {parts.map((p) => (
          <li key={p.name} className={cn('t-small', p.active ? 'font-medium text-brand' : 'text-ink-2')}>
            {p.name} {p.percent}%
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Ряд из 2–3 чисел с hairline-разделителями без переноса на узком экране (Figma К5: «542 · 97% · 13»). */
export function StatRow({ items }: { items: { value: ReactNode; label: string }[] }) {
  return (
    <dl className="m-0 grid" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
      {items.map((it, i) => (
        <div key={it.label} className={cn('flex min-w-0 flex-col-reverse justify-end gap-1', i > 0 && 'border-l border-hairline pl-4', i < items.length - 1 && 'pr-3')}>
          <dt className="truncate t-small text-ink-2">{it.label}</dt>
          <dd className="t-num-l m-0 text-ink max-[380px]:text-[28px]">{it.value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Переключатель «показывать / скрыть» (role="switch"): состояние всегда подписано текстом рядом. */
export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative h-6 w-10 shrink-0 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'bg-brand' : 'bg-steel',
      )}
    >
      <span className={cn('absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all', checked ? 'left-[18px]' : 'left-0.5')} />
    </button>
  )
}
