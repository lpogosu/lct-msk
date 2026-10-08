import { cn } from '@/lib/cn'

export interface VerifiedMarkProps {
  /** Сторона квадрата в px. */
  size?: number
  className?: string
}

/**
 * Знак «проверено» в языке «Трассы»: узел-кольцо с короткой галочкой-трассой внутри. Цвет — только бренд:
 * красный и жёлтый остаются за «против» и срокам. Подпись рядом обязательна (знак сам по себе скрыт от скринридера).
 */
export function VerifiedMark({ size = 14, className }: VerifiedMarkProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" fill="none" aria-hidden="true" focusable="false" className={cn('shrink-0', className)}>
      <circle cx="7" cy="7" r="5.6" fill="#FFFFFF" stroke="#402FFF" strokeWidth="1.1" />
      <path d="M4.3 7.2l1.9 1.9 3.5-3.9" stroke="#402FFF" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
