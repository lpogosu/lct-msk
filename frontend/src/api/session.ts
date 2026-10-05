// Сессия в памяти: access-JWT живёт только в переменной модуля (не в localStorage — XSS не достанет),
// refresh — httpOnly-cookie `vz_refresh` (путь /api/auth), ротируется бэкендом при каждом обновлении.
// Поток: register → verify-email (код + пароль) → login → refresh → me (backend/app/api/auth.py).

import type { TokenResponse, User } from './types'

export type SessionStatus = 'unknown' | 'authenticated' | 'anonymous'

export interface SessionState {
  status: SessionStatus
  accessToken: string | null
  user: User | null
  /** Когда истекает access-токен (мс, Date.now()) */
  expiresAt: number | null
}

export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

let state: SessionState = { status: 'unknown', accessToken: null, user: null, expiresAt: null }
const listeners = new Set<() => void>()
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let inflight: Promise<TokenResponse | null> | null = null

/** За сколько секунд до истечения обновляем access-токен заранее */
const REFRESH_LEEWAY_S = 30
/** Повторы refresh при 409 refresh_superseded (токен только что обновила другая вкладка) */
const SUPERSEDED_RETRIES = 2

function emit(): void {
  listeners.forEach((l) => l())
}

function set(next: SessionState): void {
  state = next
  emit()
}

// --- Подписка для useSyncExternalStore ---
export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
export function getSession(): SessionState {
  return state
}
export function getAccessToken(): string | null {
  return state.accessToken
}

function scheduleRefresh(expiresInS: number): void {
  clearTimeout(refreshTimer)
  const delay = Math.max(5, expiresInS - REFRESH_LEEWAY_S) * 1000
  refreshTimer = setTimeout(() => {
    void refreshSession()
  }, delay)
}

/** Принять ответ входа/обновления (TokenResponse). */
export function applyTokenResponse(t: TokenResponse): void {
  set({
    status: 'authenticated',
    accessToken: t.access_token,
    user: t.user,
    expiresAt: Date.now() + t.expires_in * 1000,
  })
  scheduleRefresh(t.expires_in)
}

export function clearSession(): void {
  clearTimeout(refreshTimer)
  set({ status: 'anonymous', accessToken: null, user: null, expiresAt: null })
}

export function updateUser(user: User): void {
  set({ ...state, user })
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function doRefresh(attempt = 0): Promise<TokenResponse | null> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json' },
    })
  } catch {
    // сеть недоступна: не разлогиниваем уже вошедшего, пользователь может повторить позже
    if (state.status === 'unknown') set({ ...state, status: 'anonymous' })
    return null
  }
  if (res.ok) {
    const body = (await res.json()) as TokenResponse
    applyTokenResponse(body)
    return body
  }
  if (res.status === 409 && attempt < SUPERSEDED_RETRIES) {
    await sleep(250 * (attempt + 1))
    return doRefresh(attempt + 1)
  }
  if (res.status === 401 || res.status === 403) clearSession()
  else if (state.status === 'unknown') set({ ...state, status: 'anonymous' })
  return null
}

/** Обновить сессию по refresh-cookie. Параллельные вызовы делят один запрос (ротация токена одноразовая). */
export function refreshSession(): Promise<TokenResponse | null> {
  inflight ??= doRefresh().finally(() => {
    inflight = null
  })
  return inflight
}
