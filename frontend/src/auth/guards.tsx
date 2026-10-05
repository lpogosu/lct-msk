import { useEffect, type ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { refreshSession, getSession, clearSession } from '@/api/session'
import type { Role } from '@/api/types'
import { Spinner } from '@/ui'
import { hasSessionHint, setSessionHint } from './sessionHint'
import { homePathForRole, useAuth } from './useAuth'

/** Запускает восстановление сессии при старте приложения (refresh-cookie → новый access в памяти). */
export function AuthBootstrap({ children }: { children: ReactNode }) {
  const { status, isAuthenticated } = useAuth()

  useEffect(() => {
    if (getSession().status !== 'unknown') return
    if (hasSessionHint()) {
      void refreshSession().then((t) => {
        if (!t) setSessionHint(false)
      })
    } else {
      clearSession()
    }
  }, [])

  useEffect(() => {
    if (isAuthenticated) setSessionHint(true)
    else if (status === 'anonymous') setSessionHint(false)
  }, [isAuthenticated, status])

  return <>{children}</>
}

export function FullPageLoading() {
  return (
    <div className="flex min-h-dvh items-center justify-center text-ink-3">
      <Spinner label="Загрузка" className="scale-150" />
    </div>
  )
}

/** Пускает только указанные роли. Гостя — на вход, чужую роль — в её собственный кабинет. */
export function RequireRole({ roles, children }: { roles: readonly Role[]; children?: ReactNode }) {
  const { status, role } = useAuth()
  const location = useLocation()

  if (status === 'unknown') return <FullPageLoading />
  if (status === 'anonymous') {
    return <Navigate to="/auth/login" replace state={{ from: location.pathname + location.search }} />
  }
  if (!role || !roles.includes(role)) return <Navigate to={homePathForRole(role)} replace />
  return <>{children ?? <Outlet />}</>
}

/** Экраны входа/регистрации: уже вошедшего отправляем в кабинет. */
export function RedirectIfAuthed({ children }: { children?: ReactNode }) {
  const { status, role } = useAuth()
  const location = useLocation()
  if (status === 'unknown') return <FullPageLoading />
  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from && from.startsWith('/') ? from : homePathForRole(role)} replace />
  }
  return <>{children ?? <Outlet />}</>
}
