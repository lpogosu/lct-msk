import type { Schemas } from '@/api/types'
import { cn } from '@/lib/cn'
import { VerifiedMark } from '@/ui'

type Trust = Pick<Schemas['CompanyTrustOut'], 'level' | 'label' | 'hint'>

/**
 * Метка «Проверенная компания» (добровольная; ничего не блокирует). Уровни: «Реквизиты проверены», «Домен подтверждён».
 * Без метки (level = none) ничего не рисуем: отсутствие метки — не вывод о компании (docs/TRUST_AND_SAFETY.md).
 */
export function CompanyTrustBadge({ trust, className }: { trust?: Trust | null; className?: string }) {
  if (!trust || trust.level === 'none') return null
  return (
    <span
      title={trust.hint ?? undefined}
      className={cn('inline-flex items-center gap-1.5 whitespace-nowrap rounded-pill bg-tint px-2.5 py-0.5 t-small text-brand', className)}
    >
      <VerifiedMark size={13} />
      {trust.label}
    </span>
  )
}
