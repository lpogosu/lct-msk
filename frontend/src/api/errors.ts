// Единый формат ошибок бэкенда: {code, message (рус.), details}. Фронтенд выбирает подсказку по code,
// а message показывает как есть — он уже на русском (см. backend/app/core/errors.py).

export interface FieldIssue {
  field: string | null
  message: string
  type?: string | null
}

export class ApiException extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message)
    this.name = 'ApiException'
    this.status = status
    this.code = code
    this.details = details
  }

  /** Сколько секунд ждать до повтора (429, resend_too_soon…) */
  get retryAfterS(): number | null {
    const d = this.details
    if (d && typeof d === 'object' && 'retry_after_s' in d) {
      const v = Number((d as { retry_after_s: unknown }).retry_after_s)
      return Number.isFinite(v) ? v : null
    }
    return null
  }

  /** Сколько попыток ввода осталось (code_invalid, password_mismatch) */
  get attemptsLeft(): number | null {
    const d = this.details
    if (d && typeof d === 'object' && 'attempts_left' in d) {
      const v = Number((d as { attempts_left: unknown }).attempts_left)
      return Number.isFinite(v) ? v : null
    }
    return null
  }

  /** Ошибки по полям формы (422 validation_error) */
  get fieldIssues(): FieldIssue[] {
    if (this.code !== 'validation_error' || !Array.isArray(this.details)) return []
    return (this.details as FieldIssue[]).filter((d) => d && typeof d.message === 'string')
  }

  /** Раздел бэкенда ещё не готов (501) — интерфейс показывает спокойное «Раздел в работе» */
  get isNotImplemented(): boolean {
    return this.status === 501 || this.code === 'not_implemented'
  }
}

export function isApiException(e: unknown): e is ApiException {
  return e instanceof ApiException
}

/** Превращает тело ошибки openapi-fetch (`error`) в ApiException. */
export function toApiException(status: number, body: unknown): ApiException {
  if (body && typeof body === 'object') {
    const b = body as { code?: unknown; message?: unknown; details?: unknown }
    return new ApiException(
      status,
      typeof b.code === 'string' ? b.code : 'http_error',
      typeof b.message === 'string' ? b.message : defaultMessage(status),
      b.details ?? null,
    )
  }
  return new ApiException(status, 'http_error', defaultMessage(status))
}

function defaultMessage(status: number): string {
  if (status === 0) return 'Нет связи с сервером. Проверьте интернет и повторите.'
  if (status >= 500) return 'Внутренняя ошибка сервера. Мы уже разбираемся.'
  if (status === 401) return 'Требуется вход в систему.'
  if (status === 403) return 'Недостаточно прав для этого действия.'
  if (status === 404) return 'Не найдено.'
  return 'Не удалось выполнить запрос.'
}

/** Текст для показа пользователю из любой пойманной ошибки. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiException) return e.message
  if (e instanceof TypeError) return 'Нет связи с сервером. Проверьте интернет и повторите.'
  return 'Что-то пошло не так. Повторите попытку.'
}
