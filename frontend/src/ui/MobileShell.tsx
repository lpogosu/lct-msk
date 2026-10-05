import type { ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { Trace } from './Trace'
import type { NavItem } from './TopNav'

/**
 * Рамка мобильного экрана кандидата (Figma: 360 px, поля 24). Колонка 360–430 px по центру;
 * на широком экране она остаётся колонкой — кабинет кандидата рассчитан на телефон (mobile-first).
 */
export function MobileShell({
  children,
  bottomNav,
  className,
}: {
  children: ReactNode
  bottomNav?: ReactNode
  className?: string
}) {
  return (
    <div className="min-h-dvh bg-white">
      <div className="mx-auto flex min-h-dvh w-full max-w-[430px] flex-col bg-white md:border-x md:border-hairline">
        <div className={cn('flex flex-1 flex-col px-6 pb-8 pt-5', className)}>{children}</div>
        {bottomNav}
      </div>
    </div>
  )
}

/** Верхняя строка экрана: «‹ Приглашение» + справа таймер или действие (Figma 29:3, 34:3). */
export function MobileHeader({
  title,
  backTo,
  onBack,
  right,
  className,
}: {
  title: ReactNode
  backTo?: string
  onBack?: () => void
  right?: ReactNode
  className?: string
}) {
  const chevron = (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M12.5 4.5L7 10l5.5 5.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
  const backClass = '-ml-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-ink hover:bg-panel'
  return (
    <div className={cn('mb-6 flex min-h-10 items-center gap-1', className)}>
      {backTo ? (
        <Link to={backTo} aria-label="Назад" className={backClass}>
          {chevron}
        </Link>
      ) : onBack ? (
        <button type="button" onClick={onBack} aria-label="Назад" className={backClass}>
          {chevron}
        </button>
      ) : null}
      <p className="min-w-0 flex-1 truncate t-label text-ink">{title}</p>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  )
}

/** Нижняя панель вкладок кабинета кандидата. Активная вкладка — чернила и короткая трасса. */
export function MobileTabBar({ items, className }: { items: NavItem[]; className?: string }) {
  return (
    <nav
      aria-label="Разделы кабинета"
      className={cn(
        'sticky bottom-0 z-30 border-t border-hairline bg-white pb-[env(safe-area-inset-bottom)]',
        className,
      )}
    >
      <ul className="m-0 grid list-none p-0" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
        {items.map((it) => (
          <li key={it.to}>
            <NavLink
              to={it.to}
              end={it.end}
              className="flex h-14 flex-col items-center justify-center gap-1 no-underline"
            >
              {({ isActive }) => (
                <>
                  <span className={cn('t-small', isActive ? 'font-medium text-ink' : 'text-ink-3')}>
                    {it.label}
                    {it.badge ? (
                      <>
                        <span aria-hidden="true" className="ml-1 font-mono tabular-nums">
                          {it.badge}
                        </span>
                        <span className="sr-only">, новых: {it.badge}</span>
                      </>
                    ) : null}
                  </span>
                  <Trace className={cn('w-8', !isActive && 'invisible')} height={6} inset={3} />
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
