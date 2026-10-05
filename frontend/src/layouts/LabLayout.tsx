import { Outlet } from 'react-router-dom'
import { LAB_NAV } from '@/app/nav'
import { useAuth } from '@/auth/useAuth'
import { TopNav } from '@/ui'
import { AccountMenu } from './AccountMenu'

/** Лаборатория качества теста (методист, жюри): та же шапка, что у работодателя, свои разделы (Figma 39:3). */
export function LabLayout() {
  const { user } = useAuth()
  return (
    <div className="min-h-dvh bg-white">
      <TopNav
        items={LAB_NAV}
        homeTo="/lab"
        account={{
          name: user?.role === 'admin' ? 'Администратор' : 'Методист',
          caption: 'лаборатория качества теста',
        }}
        accountMenu={user ? <AccountMenu email={user.email} /> : undefined}
      />
      <main id="main" className="mx-auto w-full max-w-page px-4 pb-12 pt-8 sm:px-6 lg:px-12 lg:pt-10">
        <Outlet />
      </main>
    </div>
  )
}
