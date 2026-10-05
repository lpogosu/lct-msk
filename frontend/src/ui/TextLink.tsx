import type { AnchorHTMLAttributes } from 'react'
import { Link, type LinkProps } from 'react-router-dom'
import { cn } from '@/lib/cn'

const BASE = 'text-brand t-label underline-offset-4 hover:underline'

/** Текстовая ссылка (фиолетовая, Onest Medium 14). Внутри приложения — `to`, наружу — `href`. */
export function TextLink({ className, ...props }: LinkProps) {
  return <Link {...props} className={cn(BASE, className)} />
}

export function ExternalLink({ className, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return <a rel="noreferrer noopener" target="_blank" {...props} className={cn(BASE, className)} />
}
