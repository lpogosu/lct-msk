import { formatInt } from '@/lib/format'

/**
 * Вилка и «ваш минимум» на одной трассе (Figma К10): два фиолетовых узла — границы вилки, засечка — минимум кандидата.
 * Масштаб считается от вилки и минимума с полем по краям, чтобы засечка не уходила за экран.
 * Минимум в другой «базе» (на руки / до НДФЛ) не рисуем: сравнение было бы неверным — вместо засечки текст.
 */
export function SalaryTrace({ from, to, min }: { from: number; to: number; min?: number | null }) {
  const lo0 = Math.min(from, min ?? from)
  const hi0 = Math.max(to, min ?? to)
  const pad = Math.max((hi0 - lo0) * 0.25, 10_000)
  const lo = lo0 - pad
  const hi = hi0 + pad
  const pos = (v: number) => ((v - lo) / (hi - lo)) * 100
  const label = (v: number) => (v >= 10_000 ? `${formatInt(Math.round(v / 1000))}` : formatInt(v))

  return (
    <div
      role="img"
      aria-label={`Вилка ${formatInt(from)}–${formatInt(to)} ₽` + (min != null ? `, ваш минимум ${formatInt(min)} ₽` : '')}
      className="relative h-12 w-full"
    >
      <span className="absolute inset-x-0 top-[14px] h-px bg-steel" />
      <span className="absolute top-[13px] h-[3px] rounded-full bg-brand" style={{ left: `${pos(from)}%`, width: `${pos(to) - pos(from)}%` }} />
      <span className="absolute top-[14px] h-[9px] w-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand" style={{ left: `${pos(from)}%` }} />
      <span className="absolute top-[14px] h-[9px] w-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand" style={{ left: `${pos(to)}%` }} />
      {min != null && (
        <>
          <span className="absolute top-[6px] h-4 w-px bg-ink" style={{ left: `${pos(min)}%` }} />
          <span
            className="absolute top-[26px] -translate-x-1/2 whitespace-nowrap t-small text-ink-2"
            style={{ left: `clamp(48px, ${pos(min)}%, calc(100% - 48px))` }}
          >
            ваш минимум {label(min)}
          </span>
        </>
      )}
    </div>
  )
}
