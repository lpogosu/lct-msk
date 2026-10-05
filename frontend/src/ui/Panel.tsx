import type { ElementType, HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'

export type PanelVariant = 'panel' | 'tint' | 'hero' | 'outline'
export type PanelRadius = 'md' | 'lg' | 'xl' | 'hero'

export interface PanelProps extends HTMLAttributes<HTMLElement> {
  /** panel — серая плашка #F6F6F8; tint — светло-фиолетовая; hero — мягкий градиент tint→white (единственный градиент); outline — только hairline */
  variant?: PanelVariant
  /** 12 · 16 · 20 · 24 px (в Figma: панель кандидата 12, поле брифа 16, «Как мы поняли» 20, hero 24) */
  radius?: PanelRadius
  /** Внутренний отступ: none · sm 16 · md 24 · lg 28–32 */
  padding?: 'none' | 'sm' | 'md' | 'lg'
  as?: ElementType
  children?: ReactNode
}

const VARIANT: Record<PanelVariant, string> = {
  panel: 'bg-panel',
  tint: 'bg-tint',
  hero: 'bg-gradient-to-r from-tint to-white',
  outline: 'bg-white border border-hairline',
}
const RADIUS: Record<PanelRadius, string> = {
  md: 'rounded-panel',
  lg: 'rounded-panel-lg',
  xl: 'rounded-panel-xl',
  hero: 'rounded-hero',
}
const PADDING: Record<NonNullable<PanelProps['padding']>, string> = {
  none: '',
  sm: 'p-4',
  md: 'p-5 sm:p-6',
  lg: 'p-5 sm:p-7',
}

/** Панель без рамок и теней: поверхность задаёт только цвет (hairline вместо границ). */
export function Panel({
  variant = 'panel',
  radius = 'md',
  padding = 'lg',
  as: Tag = 'div',
  className,
  children,
  ...rest
}: PanelProps) {
  return (
    <Tag className={cn(VARIANT[variant], RADIUS[radius], PADDING[padding], className)} {...rest}>
      {children}
    </Tag>
  )
}
