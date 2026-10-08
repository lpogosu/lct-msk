import { Outlet } from 'react-router-dom'
import { FSP_NAV } from '@/app/nav'
import { useAuth } from '@/auth/useAuth'
import { TopNav } from '@/ui'
import { AccountMenu } from './AccountMenu'

/** Кабинет ФСП: та же шапка «Трассы», один раздел. Доступ — методист и администратор (демо: кнопка «Панель ФСП» на /jury). */
export function FspLayout() {
  const { user } = useAuth()
  return (
    <div className="min-h-dvh bg-white">
      <TopNav
        items={FSP_NAV}
        homeTo="/fsp/reserve"
        account={{ name: 'Федерация спортивного программирования', caption: 'кадровый резерв' }}
        accountMenu={user ? <AccountMenu email={user.email} /> : undefined}
      />
      <main id="main" className="mx-auto w-full max-w-page px-4 pb-12 pt-8 sm:px-6 lg:px-12 lg:pt-10">
        <Outlet />
      </main>
    </div>
  )
}
