// Подсказка «сессия, вероятно, есть»: не секрет и не токен, только чтобы не слать заведомо
// бесполезный refresh (и не получать 401 в консоль) у человека, который ни разу не входил.
const HINT_KEY = 'vz_session_hint'

export function setSessionHint(on: boolean): void {
  try {
    if (on) localStorage.setItem(HINT_KEY, '1')
    else localStorage.removeItem(HINT_KEY)
  } catch {
    /* приватный режим — не критично */
  }
}

export function hasSessionHint(): boolean {
  try {
    return localStorage.getItem(HINT_KEY) === '1'
  } catch {
    return true // прочитать не можем — пробуем refresh, он безопасен
  }
}
