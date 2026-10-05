import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { Logo } from './Logo'
import { Trace } from './Trace'

export interface NavItem {
  to: string
  label: string
  /** Счётчик рядом с названием: «Приглашения 2» */
  badge?: number
  /** Точное совпадение пути (для корневого пункта раздела) */
  end?: boolean
}

export interface TopNavAccount {
  /** Название компании или имя: 14 px Medium */
  name: string
  /** Вторая строка: «57 из 60 приглашений на неделе» */
  caption?: string
}

export interface TopNavProps {
  items: NavItem[]
  homeTo?: string
  account?: TopNavAccount
  /** Содержимое меню аккаунта (кнопка «Выйти» и т. п.); без него блок аккаунта — просто текст */
  accountMenu?: ReactNode
  className?: string
}

function ItemBadge({ n }: { n: number }) {
  return (
    <>
      <span aria-hidden="true" className="ml-1.5 font-mono tabular-nums">
        {n}
      </span>
      <span className="sr-only">, новых: {n}</span>
    </>
  )
}

/**
 * Верхняя навигация кабинета работодателя и лаборатории (Figma 27:3 «Шапка», 1440 px).
 * Активный пункт — чернила Medium 14 и короткая трасса-подчёркивание; остальные — серые 15.
 * Ниже lg пункты уходят в раскрывающееся меню.
 */
export function TopNav({ items, homeTo = '/', account, accountMenu, className }: TopNavProps) {
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const location = useLocation()

  // закрываем мобильное меню при переходе
  useEffect(() => setOpen(false), [location.pathname])

  return (
    <header className={cn('border-b border-hairline bg-white', className)}>
      <div className="mx-auto flex max-w-page items-center gap-4 px-4 py-3.5 sm:px-6 lg:gap-10 lg:px-12 lg:py-5">
        <Logo to={homeTo} />

        <nav aria-label="Основная навигация" className="hidden min-w-0 flex-1 items-center gap-7 lg:flex">
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              end={it.end}
              className="flex flex-col items-center no-underline"
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cn(
                      'whitespace-nowrap transition-colors',
                      isActive ? 't-label text-ink' : 't-body text-ink-3 hover:text-ink',
                    )}
                  >
                    {it.label}
                    {it.badge ? <ItemBadge n={it.badge} /> : null}
                  </span>
                  {isActive && <Trace className="mt-1.5 w-10" height={6} inset={3} />}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3 lg:ml-0">
          {account && <AccountBlock account={account} menu={accountMenu} />}
          <button
            type="button"
            className="flex h-10 w-10 items-center justify-center rounded-full text-ink hover:bg-panel lg:hidden"
            aria-expanded={open}
            aria-controls={menuId}
            aria-label={open ? 'Закрыть меню' : 'Открыть меню'}
            onClick={() => setOpen((v) => !v)}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              {open ? (
                <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              ) : (
                <path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              )}
            </svg>
          </button>
        </div>
      </div>

      {open && (
        <nav
          id={menuId}
          aria-label="Основная навигация"
          className="border-t border-hairline bg-white px-4 py-2 sm:px-6 lg:hidden"
        >
          <ul className="m-0 flex list-none flex-col p-0">
            {items.map((it) => (
              <li key={it.to}>
                <NavLink
                  to={it.to}
                  end={it.end}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2 border-b border-hairline py-3 no-underline last:border-b-0',
                      isActive ? 't-label text-ink' : 't-body text-ink-2',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-brand" />}
                      <span>
                        {it.label}
                        {it.badge ? <ItemBadge n={it.badge} /> : null}
                      </span>
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </header>
  )
}

function AccountBlock({ account, menu }: { account: TopNavAccount; menu?: ReactNode }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const text = (
    <span className="flex min-w-0 flex-col items-end text-right">
      <span className="max-w-[160px] truncate t-label text-ink sm:max-w-[240px]">{account.name}</span>
      {account.caption && <span className="hidden whitespace-nowrap t-small text-ink-3 sm:block">{account.caption}</span>}
    </span>
  )

  if (!menu) return text

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="true"
        className="-mx-1 rounded-md px-1 hover:bg-panel"
      >
        {text}
      </button>
      {open && (
        <div
          id={panelId}
          className="absolute right-0 top-full z-40 mt-2 min-w-[260px] max-w-[calc(100vw-2rem)] rounded-panel border border-hairline bg-white p-2 shadow-float"
        >
          {menu}
        </div>
      )}
    </div>
  )
}
