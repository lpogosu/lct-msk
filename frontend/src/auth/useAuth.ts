import { useSyncExternalStore } from 'react'
import { getSession, subscribeSession, type SessionState } from '@/api/session'
import type { Role, User } from '@/api/types'

/** Живая сессия из памяти: перерисовывает компонент при входе, обновлении и выходе. */
export function useSession(): SessionState {
  return useSyncExternalStore(subscribeSession, getSession, getSession)
}

export interface Auth {
  status: SessionState['status']
  user: User | null
  role: Role | null
  isAuthenticated: boolean
}

export function useAuth(): Auth {
  const s = useSession()
  return {
    status: s.status,
    user: s.user,
    role: (s.user?.role as Role | undefined) ?? null,
    isAuthenticated: s.status === 'authenticated',
  }
}

/** Куда вести пользователя после входа: у каждой роли свой кабинет. */
export function homePathForRole(role: string | null | undefined): string {
  switch (role) {
    case 'candidate':
      return '/candidate'
    case 'employer':
      return '/employer'
    case 'methodist':
    case 'admin':
      return '/lab'
    default:
      return '/'
  }
}

export const ROLE_LABEL: Record<Role, string> = {
  candidate: 'Кандидат',
  employer: 'Работодатель',
  methodist: 'Методист',
  admin: 'Администратор',
}
