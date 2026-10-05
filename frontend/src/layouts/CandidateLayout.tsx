import { Outlet, useMatches } from 'react-router-dom'
import { CANDIDATE_NAV } from '@/app/nav'
import { MobileShell, MobileTabBar } from '@/ui'
import type { RouteHandle } from './RootLayout'

/**
 * Кабинет кандидата — mobile-first (Figma 360 px): колонка до 430 px и нижние вкладки.
 * Экраны сами рисуют верхнюю строку через <MobileHeader />; на экранах с `handle.hideNav`
 * (тест) вкладки скрыты, чтобы не отвлекать.
 */
export function CandidateLayout() {
  const matches = useMatches()
  const hideNav = matches.some((m) => (m.handle as RouteHandle | undefined)?.hideNav)
  return (
    <MobileShell bottomNav={hideNav ? undefined : <MobileTabBar items={CANDIDATE_NAV} />}>
      <main id="main" className="flex flex-1 flex-col">
        <Outlet />
      </main>
    </MobileShell>
  )
}
