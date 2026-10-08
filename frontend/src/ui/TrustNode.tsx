import { cn } from '@/lib/cn'

/**
 * Узел доверия — форма узла говорит, чем подтверждено утверждение (Figma «Трасса», легенда 32:115):
 *   fsp      — залитый круг: достижение из реестра ФСП
 *   test     — кольцо с точкой: подтверждено адаптивным тестом
 *   resume   — пустое кольцо: из резюме
 *   claimed  — пунктирное кольцо: заявлено кандидатом, не проверено
 */
export type NodeKind = 'fsp' | 'test' | 'resume' | 'claimed'

export const NODE_LABEL: Record<NodeKind, string> = {
  fsp: 'ФСП',
  test: 'тест',
  resume: 'резюме',
  claimed: 'заявлено',
}

export const NODE_DESCRIPTION: Record<NodeKind, string> = {
  fsp: 'подтверждено достижением ФСП',
  test: 'подтверждено тестом',
  resume: 'указано в резюме',
  claimed: 'заявлено, не проверено',
}

const BRAND = '#402FFF'
const INK3 = '#686970'

export interface TrustNodeProps {
  kind: NodeKind
  /** Сторона квадрата в px. 14 — в легенде и списках, 20 — на концах трасс (геометрия масштабируется) */
  size?: number
  /** Цвет заливки колец: должен совпадать с фоном, чтобы линия трассы не просвечивала внутри узла */
  surface?: string
  /** Если узел несёт смысл сам по себе (нет подписи рядом) — задайте подпись для скринридера */
  label?: string
  className?: string
}

export function TrustNode({ kind, size = 14, surface = '#FFFFFF', label, className }: TrustNodeProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 14 14"
      fill="none"
      className={cn('shrink-0', className)}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {kind === 'fsp' && <circle cx="7" cy="7" r="3.5" fill={BRAND} />}
      {kind === 'test' && (
        <>
          <circle cx="7" cy="7" r="3.85" fill={surface} stroke={BRAND} strokeWidth="1.05" />
          <circle cx="7" cy="7" r="1.54" fill={BRAND} />
        </>
      )}
      {kind === 'resume' && <circle cx="7" cy="7" r="3.5" fill={surface} stroke={INK3} strokeWidth="1.05" />}
      {kind === 'claimed' && (
        <circle cx="7" cy="7" r="3.5" fill={surface} stroke={INK3} strokeWidth="1.05" strokeDasharray="1.4 1.4" />
      )}
    </svg>
  )
}

/** Легенда: «● ФСП  ◉ тест  ○ резюме  ◌ заявлено» + подпись «Чем сильнее доказательство, тем плотнее узел». */
export function NodeLegend({
  className,
  surface,
  kinds = ['fsp', 'test', 'resume', 'claimed'],
  caption = true,
}: {
  className?: string
  surface?: string
  kinds?: NodeKind[]
  caption?: boolean | string
}) {
  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {kinds.map((k) => (
          <li key={k} className="flex items-center gap-1.5 t-small text-ink-2">
            <TrustNode kind={k} surface={surface} />
            {NODE_LABEL[k]}
          </li>
        ))}
      </ul>
      {caption && (
        <p className="t-small text-ink-3">
          {typeof caption === 'string' ? caption : 'Чем сильнее доказательство, тем плотнее узел'}
        </p>
      )}
    </div>
  )
}
