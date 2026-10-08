import type { NavItem } from '@/ui'

// Пункты навигации по ролям (Figma «Шапка»: 27:3, 39:3, 42:3). Меняются только здесь.

export const EMPLOYER_NAV: NavItem[] = [
  { to: '/employer/needs', label: 'Потребности' },
  { to: '/employer/bank', label: 'Банк кандидатов' },
  { to: '/employer/invitations', label: 'Приглашения' },
  { to: '/employer/vacancies', label: 'Вакансии' },
  { to: '/employer/tasks', label: 'Задачи' },
]

export const LAB_NAV: NavItem[] = [
  { to: '/lab/sessions', label: 'Сессии' },
  { to: '/lab/bank', label: 'Банк заданий' },
  { to: '/lab/quality', label: 'Качество' },
  { to: '/lab/reconciliations', label: 'Сверки' },
  { to: '/lab/validation', label: 'Валидация' },
  { to: '/lab/trust', label: 'Жалобы' },
]

export const FSP_NAV: NavItem[] = [
  { to: '/fsp/reserve', label: 'Кадровый резерв' },
  { to: '/lab/validation', label: 'Как мы считаем' },
]

export const CANDIDATE_NAV: NavItem[] = [
  { to: '/candidate', label: 'Мой путь', end: true },
  { to: '/candidate/invitations', label: 'Приглашения' },
  { to: '/candidate/profile', label: 'Профиль' },
]
