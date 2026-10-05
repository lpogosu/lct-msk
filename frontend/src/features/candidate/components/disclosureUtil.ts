import type { PrivacyOut } from '@/features/candidate/queries'

export const FIELD_LABELS: Record<string, string> = {
  full_name: 'ФИО',
  photo_url: 'Фото',
  email: 'Email',
  phone: 'Телефон',
  telegram: 'Telegram',
  resume: 'Резюме',
  current_employer: 'Текущий работодатель',
  university: 'Вуз и образование',
}

export const CONTACT_FIELDS = ['email', 'phone', 'telegram']

export interface FieldOption {
  field: string
  label: string
  contact: boolean
}

/** Поля, которые можно раскрыть компании: из настроек К13 (с подписями), иначе — стандартный набор. */
export function fieldOptions(privacy: PrivacyOut | undefined, defaults: readonly string[]): { options: FieldOption[]; initial: Record<string, boolean> } {
  const fromPrivacy = privacy?.field_visibility ?? []
  if (fromPrivacy.length > 0) {
    return {
      options: fromPrivacy.map((f) => ({ field: f.field, label: f.label, contact: f.contact })),
      initial: Object.fromEntries(fromPrivacy.map((f) => [f.field, f.allowed])),
    }
  }
  return {
    options: defaults.map((f) => ({ field: f, label: FIELD_LABELS[f] ?? f, contact: CONTACT_FIELDS.includes(f) })),
    initial: Object.fromEntries(defaults.map((f) => [f, true])),
  }
}

export const chosen = (m: Record<string, boolean>): string[] => Object.entries(m).filter(([, on]) => on).map(([k]) => k)
export const hasContact = (m: Record<string, boolean>): boolean => CONTACT_FIELDS.some((k) => m[k])
