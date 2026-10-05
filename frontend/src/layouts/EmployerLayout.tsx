import { useMemo } from 'react'
import { Link, Outlet } from 'react-router-dom'
import { EMPLOYER_NAV } from '@/app/nav'
import { useAuth } from '@/auth/useAuth'
import { useCompany, useKanban } from '@/features/employer/api'
import { TopNav, type NavItem } from '@/ui'
import { AccountMenu } from './AccountMenu'

/**
 * Кабинет работодателя — десктоп (Figma 1440 px): верхняя навигация и контент до 1440 px.
 * В шапке — название компании и остаток недельной квоты приглашений (GET /api/companies/me),
 * у «Приглашений» — число встречных условий, ждущих ответа.
 */
export function EmployerLayout() {
  const { user } = useAuth()
  const company = useCompany()
  const kanban = useKanban()

  const waiting = kanban.data?.columns.find((c) => c.status === 'countered')?.count ?? 0
  const items = useMemo<NavItem[]>(
    () => EMPLOYER_NAV.map((it) => (it.to.endsWith('/invitations') && waiting > 0 ? { ...it, badge: waiting } : it)),
    [waiting],
  )

  const me = company.data
  const name = me?.name?.trim() || user?.email || 'Работодатель'
  const caption = me
    ? `${me.quota.remaining} из ${me.quota.limit} приглашений на неделе`
    : 'кабинет работодателя'

  return (
    <div className="min-h-dvh bg-white">
      <TopNav
        items={items}
        homeTo="/employer"
        account={{ name, caption }}
        accountMenu={
          user ? (
            <div className="flex flex-col">
              <Link
                to="/employer/company"
                className="mx-1 rounded-md px-2 py-2 t-label text-ink no-underline hover:bg-panel"
              >
                Профиль компании
                {me && !me.profile_complete && <span className="ml-2 t-small text-danger-text">нужно заполнить</span>}
              </Link>
              <AccountMenu email={user.email} />
            </div>
          ) : undefined
        }
      />
      <main id="main" className="mx-auto w-full max-w-page px-4 pb-12 pt-8 sm:px-6 lg:px-12 lg:pt-10">
        <Outlet />
      </main>
    </div>
  )
}
