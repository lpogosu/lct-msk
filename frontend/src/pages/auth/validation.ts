// Клиентские проверки — только чтобы подсказать до отправки. Решает бэкенд (422 с русскими сообщениями по полям).

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/

export function validateEmail(value: string): string | null {
  const v = value.trim()
  if (!v) return 'Введите email'
  if (!EMAIL_RE.test(v)) return 'Похоже на опечатку: проверьте адрес электронной почты'
  return null
}

/** Требования бэкенда: минимум 8 символов, хотя бы одна буква и одна цифра. */
export function validateNewPassword(value: string): string | null {
  if (!value) return 'Придумайте пароль'
  if (value.length < 8) return 'Минимум 8 символов'
  if (!/\p{L}/u.test(value) || !/\d/.test(value)) return 'Нужна хотя бы одна буква и одна цифра'
  return null
}

export function validateCode(value: string): string | null {
  if (!/^\s*\d{6}\s*$/.test(value)) return 'Код — 6 цифр из письма'
  return null
}

/** Год рождения кандидата. Порог возраста (15 лет) — на бэкенде, config/contact.yaml → minors; здесь только формат. */
export function validateBirthYear(value: string): string | null {
  const year = Number(value)
  if (!/^\d{4}$/.test(value.trim())) return 'Укажите год рождения четырьмя цифрами'
  if (year < 1940 || year > new Date().getFullYear()) return 'Проверьте год рождения'
  return null
}
