import createClient from 'openapi-fetch'
import { ApiException, toApiException } from './errors'
import { API_BASE, getAccessToken, refreshSession } from './session'
import type { paths } from './schema'

// Эндпоинты, где 401 — штатный ответ (неверный пароль, нет cookie…): там обновлять сессию и повторять нельзя.
const NO_REFRESH_RETRY = /\/api\/auth\/(login|register|verify-email|resend-code|refresh|logout|password-reset)/

/**
 * fetch с авторизацией: подставляет Bearer из памяти, а на 401 один раз обновляет сессию по refresh-cookie
 * и повторяет запрос. Клон запроса делаем до отправки — тело одноразовое.
 */
async function authFetch(req: Request): Promise<Response> {
  const retry = req.clone()
  const token = getAccessToken()
  if (token) req.headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(req)
  if (res.status !== 401 || NO_REFRESH_RETRY.test(new URL(req.url, window.location.origin).pathname)) return res

  const refreshed = await refreshSession()
  if (!refreshed) return res
  retry.headers.set('Authorization', `Bearer ${refreshed.access_token}`)
  return fetch(retry)
}

/**
 * Типизированный клиент по OpenAPI бэкенда (`npm run gen:api`). Пути содержат префикс /api:
 *   const { data, error } = await api.GET('/api/auth/me')
 * В компонентах удобнее `unwrap(api.GET(...))` — бросает ApiException с русским сообщением.
 */
export const api = createClient<paths>({
  baseUrl: API_BASE,
  credentials: 'include',
  fetch: authFetch,
})

interface FetchResult<D, E> {
  data?: D
  error?: E
  response: Response
}

/** Ждёт ответ openapi-fetch и возвращает data; ошибки превращает в ApiException (для TanStack Query и форм). */
export async function unwrap<D, E>(request: Promise<FetchResult<D, E>>): Promise<D> {
  let result: FetchResult<D, E>
  try {
    result = await request
  } catch {
    throw new ApiException(0, 'network_error', 'Нет связи с сервером. Проверьте интернет и повторите.')
  }
  if (result.error !== undefined || !result.response.ok) {
    throw toApiException(result.response.status, result.error)
  }
  return result.data as D
}
