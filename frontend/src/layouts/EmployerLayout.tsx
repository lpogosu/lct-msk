import { Outlet } from 'react-router-dom'
import { EMPLOYER_NAV } from '@/app/nav'
import { useAuth } from '@/auth/useAuth'
import { TopNav } from '@/ui'
import { AccountMenu } from './AccountMenu'

/**
 * Кабинет работодателя — десктоп (Figma 1440 px): верхняя навигация и контент до 1440 px.
 * Название компании и квота приглашений подключит экран «Потребности» через /api/companies.
 */
export function EmployerLayout() {
  const { user } = useAuth()
  return (
    <div className="min-h-dvh bg-white">
      <TopNav
        items={EMPLOYER_NAV}
        homeTo="/employer"
        account={{ name: user?.email ?? 'Работодатель', caption: 'кабинет работодателя' }}
        accountMenu={user ? <AccountMenu email={user.email} /> : undefined}
      />
      <main id="main" className="mx-auto w-full max-w-page px-4 pb-12 pt-8 sm:px-6 lg:px-12 lg:pt-10">
        <Outlet />
      </main>
    </div>
  )
}
