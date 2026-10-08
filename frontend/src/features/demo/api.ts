import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { applyTokenResponse } from '@/api/session'
import type { Schemas } from '@/api/types'

// Запросы страницы жюри /jury (backend/app/api/demo.py, DEMO_MODE). Без DEMO_MODE бэкенд отвечает 404.

export type JuryPaths = Schemas['JuryPathsOut']
export type JuryMode = 'express' | 'full'

/** Включён ли DEMO_MODE: берём из /api/health (открытый маршрут). Пока неизвестно — false, чтобы не мигать ссылкой. */
export function useDemoMode(): boolean {
  const q = useQuery({
    queryKey: ['health', 'demo-mode'],
    queryFn: () => unwrap(api.GET('/api/health')),
    staleTime: 5 * 60_000,
    retry: false,
  })
  return q.data?.demo_mode === true
}

export function useJuryPaths() {
  return useQuery({ queryKey: ['demo', 'jury-paths'], queryFn: () => unwrap(api.GET('/api/demo/jury/paths')), retry: false })
}

/** Одноразовый кандидат + начатый тест; сессия открывается сразу (токен в память, refresh — cookie). */
export async function startJuryTest(mode: JuryMode, specialization: string, grade: string) {
  const t = await unwrap(api.POST('/api/demo/jury/start', { body: { mode, specialization, grade } }))
  applyTokenResponse(t)
  return t
}

/** Вход без пароля в демо-роль: работодатель, лаборатория жюри или панель ФСП (тот же аккаунт жюри). */
export async function enterJuryPersona(persona: 'employer' | 'lab' | 'fsp') {
  const t = await unwrap(api.POST('/api/demo/jury/persona', { body: { persona } }))
  applyTokenResponse(t)
  return t
}
