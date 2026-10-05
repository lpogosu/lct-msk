import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react'
import { Link, type LinkProps } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'outline' | 'text'
export type ButtonSize = 'sm' | 'md' | 'lg'

interface BaseProps {
  /** primary — заливка; outline — контур 1,5 px; text — только текст (второстепенное действие) */
  variant?: ButtonVariant
  /** sm 40 px · md 46 px (по умолчанию) · lg 50 px */
  size?: ButtonSize
  /** на всю ширину контейнера */
  block?: boolean
  /** показывает индикатор и блокирует повторное нажатие */
  loading?: boolean
  children?: ReactNode
}

type AsButton = BaseProps &
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'color'> & { to?: undefined; href?: undefined }
type AsRouterLink = BaseProps & Omit<LinkProps, 'color'> & { href?: undefined }
type AsAnchor = BaseProps &
  Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'color'> & { href: string; to?: undefined }

export type ButtonProps = AsButton | AsRouterLink | AsAnchor

// Размеры из Figma: вертикальные/горизонтальные отступы заданы переменными, а у контурной кнопки
// из них вычитается толщина рамки — высота у всех вариантов одинаковая.
const SIZE: Record<ButtonSize, string> = {
  sm: '[--px:18px] [--py:10px]',
  md: '[--px:24px] [--py:13px]',
  lg: '[--px:26px] [--py:15px]',
}

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    '[--bw:0px] bg-brand text-white hover:bg-brand-hover active:bg-brand-press disabled:bg-hairline disabled:text-ink-3 aria-disabled:bg-hairline aria-disabled:text-ink-3',
  outline:
    '[--bw:1.5px] border-brand bg-transparent text-brand hover:bg-tint active:bg-heat-2 disabled:border-steel disabled:text-ink-3 disabled:hover:bg-transparent aria-disabled:border-steel aria-disabled:text-ink-3',
  text: '[--bw:0px] [--px:0px] [--py:2px] rounded-sm text-brand hover:underline underline-offset-4 disabled:text-ink-3 aria-disabled:text-ink-3',
}

export function buttonClasses({
  variant = 'primary',
  size = 'md',
  block,
  className,
}: Pick<BaseProps, 'variant' | 'size' | 'block'> & { className?: string }): string {
  return cn(
    'inline-flex items-center justify-center gap-2 whitespace-nowrap font-sans t-label no-underline',
    'select-none transition-colors duration-150 disabled:cursor-not-allowed aria-disabled:cursor-not-allowed',
    variant !== 'text' && 'rounded-pill border-solid border-[length:var(--bw)] px-[calc(var(--px)_-_var(--bw))] py-[calc(var(--py)_-_var(--bw))]',
    variant !== 'text' && 'border-transparent',
    SIZE[size],
    VARIANT[variant],
    block && 'w-full',
    className,
  )
}

export function Button(props: ButtonProps) {
  const { variant = 'primary', size = 'md', block, loading, className, children, ...rest } = props
  const classes = buttonClasses({ variant, size, block, className })
  const content = (
    <>
      {loading && <Spinner className="-ml-1" />}
      {children}
    </>
  )

  if ('to' in rest && rest.to !== undefined) {
    return (
      <Link {...(rest as LinkProps)} className={classes}>
        {content}
      </Link>
    )
  }
  if ('href' in rest && rest.href !== undefined) {
    return (
      <a {...(rest as AnchorHTMLAttributes<HTMLAnchorElement>)} className={classes}>
        {content}
      </a>
    )
  }
  const { disabled, type = 'button', ...btn } = rest as ButtonHTMLAttributes<HTMLButtonElement>
  return (
    <button
      {...btn}
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {content}
    </button>
  )
}
