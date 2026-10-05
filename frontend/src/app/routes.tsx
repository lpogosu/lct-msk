import type { ReactNode } from 'react'
import { createBrowserRouter, Navigate, type RouteObject } from 'react-router-dom'
import { FullPageLoading, RedirectIfAuthed, RequireRole } from '@/auth/guards'
import { CandidateLayout } from '@/layouts/CandidateLayout'
import { EmployerLayout } from '@/layouts/EmployerLayout'
import { LabLayout } from '@/layouts/LabLayout'
import { PublicLayout } from '@/layouts/PublicLayout'
import { RootLayout } from '@/layouts/RootLayout'
import { AuthLayout } from '@/pages/auth/AuthLayout'
import Landing from '@/pages/Landing'
import { NotFound, RouteError } from '@/pages/NotFound'
import { MobileHeader, WorkInProgress } from '@/ui'

/*
 * КАРТА МАРШРУТОВ. Каждый ещё не собранный экран — заглушка `wip(...)` со спокойным «Раздел в работе»
 * и номером узла Figma (страница «v2 · Трасса», file rzq7Hlpmq8hTkGJQsJ7AoF).
 * Экран-агент заменяет `element: wip(...)` на настоящую страницу (`lazy` — чтобы не раздувать стартовый бандл).
 */

function wip(section: string, backTo?: string, figma?: string, description?: string): ReactNode {
  return <WorkInProgress section={section} backTo={backTo} figma={figma} description={description} />
}

/** Заглушка мобильного экрана кандидата: верхняя строка + «в работе» */
function mobileWip(section: string, backTo: string | undefined, figma?: string, description?: string): ReactNode {
  return (
    <>
      <MobileHeader title={section} backTo={backTo} />
      <WorkInProgress section={section} backTo={backTo} figma={figma} description={description} />
    </>
  )
}

const publicRoutes: RouteObject = {
  element: <PublicLayout />,
  children: [
    { index: true, element: <Landing />, handle: { title: 'Главная' } },
    // Публичная сверка стандартизированного PDF-профиля по QR (ссылки /v/{code} печатает бэкенд)
    { path: 'v/:code', element: wip('Сверка профиля', '/', 'Акт сверки по QR-коду'), handle: { title: 'Сверка профиля' } },
  ],
}

// Экраны входа. Адреса /verify-email и /reset-password зашиты в ссылки из писем бэкенда
// (backend/app/mail/templates.py: ?email=…&code=…) — не переименовывать без правки бэкенда.
const authRoutes: RouteObject = {
  element: (
    <RedirectIfAuthed>
      <AuthLayout />
    </RedirectIfAuthed>
  ),
  children: [
    { path: 'auth', element: <Navigate to="/auth/login" replace /> },
    { path: 'auth/login', lazy: async () => ({ Component: (await import('@/pages/auth/Login')).default }), handle: { title: 'Вход' } },
    {
      path: 'auth/register',
      lazy: async () => ({ Component: (await import('@/pages/auth/Register')).default }),
      handle: { title: 'Регистрация' },
    },
    {
      path: 'verify-email',
      lazy: async () => ({ Component: (await import('@/pages/auth/Verify')).default }),
      handle: { title: 'Код из письма' },
    },
    {
      path: 'reset-password',
      lazy: async () => ({ Component: (await import('@/pages/auth/Reset')).default }),
      handle: { title: 'Восстановление пароля' },
    },
    { path: 'auth/verify', element: <Navigate to="/verify-email" replace /> },
    { path: 'auth/reset', element: <Navigate to="/reset-password" replace /> },
  ],
}

// Кандидат — mobile-first. Figma: К4 тест 34:2, К5 результат 34:42, К10 приглашение 29:2, «Взаимно» 29:48.
const candidateRoutes: RouteObject = {
  path: 'candidate',
  element: (
    <RequireRole roles={['candidate']}>
      <CandidateLayout />
    </RequireRole>
  ),
  children: [
    { index: true, element: mobileWip('Мой путь', undefined, 'К1–К3: опрос → выбор грейда'), handle: { title: 'Мой путь' } },
    { path: 'survey', element: mobileWip('Опрос', '/candidate'), handle: { title: 'Опрос' } },
    { path: 'test', element: mobileWip('Тест', '/candidate', '34:2 К4 Тест'), handle: { title: 'Тест', hideNav: true } },
    { path: 'result', element: mobileWip('Результат теста', '/candidate', '34:42 К5 Результат'), handle: { title: 'Результат теста' } },
    { path: 'invitations', element: mobileWip('Приглашения', '/candidate'), handle: { title: 'Приглашения' } },
    {
      path: 'invitations/:id',
      element: mobileWip('Приглашение', '/candidate/invitations', '29:2 К10 Приглашение, 29:48 К10 Взаимно'),
      handle: { title: 'Приглашение', hideNav: true },
    },
    {
      path: 'profile',
      lazy: async () => ({ Component: (await import('@/pages/candidate/Profile')).default }),
      handle: { title: 'Профиль' },
    },
    { path: 'consents', element: mobileWip('Согласия', '/candidate/profile', 'К13 журнал согласий'), handle: { title: 'Согласия' } },
  ],
}

// Работодатель — десктоп. Figma: Р3 бриф 40:2, Р5 подборка 27:2, карта талантов 33:2, Р8 приглашения 42:2.
const employerRoutes: RouteObject = {
  path: 'employer',
  element: (
    <RequireRole roles={['employer']}>
      <EmployerLayout />
    </RequireRole>
  ),
  children: [
    { index: true, element: <Navigate to="needs" replace /> },
    { path: 'needs', element: wip('Потребности', undefined, undefined, 'Здесь появятся ваши потребности и версии подборок.'), handle: { title: 'Потребности' } },
    { path: 'needs/new', element: wip('Описание потребности', '/employer/needs', '40:2 Р3 Опишите, кого ищете'), handle: { title: 'Новая потребность' } },
    { path: 'needs/:id', element: wip('Подборка', '/employer/needs', '27:2 Р5 Подборка'), handle: { title: 'Подборка' } },
    { path: 'bank', element: wip('Банк кандидатов', undefined, '33:2 Карта талантов'), handle: { title: 'Банк кандидатов' } },
    { path: 'invitations', element: wip('Приглашения', undefined, '42:2 Р8 Приглашения'), handle: { title: 'Приглашения' } },
    { path: 'vacancies', element: wip('Вакансии'), handle: { title: 'Вакансии' } },
    { path: 'tasks', element: wip('Задачи'), handle: { title: 'Задачи' } },
  ],
}

// Лаборатория (методист, жюри). Figma: Акт сверки (Антиподсказчик) 39:2.
const labRoutes: RouteObject = {
  path: 'lab',
  element: (
    <RequireRole roles={['methodist', 'admin']}>
      <LabLayout />
    </RequireRole>
  ),
  children: [
    { index: true, element: <Navigate to="sessions" replace /> },
    { path: 'sessions', element: wip('Сессии'), handle: { title: 'Сессии' } },
    { path: 'bank', element: wip('Банк заданий'), handle: { title: 'Банк заданий' } },
    { path: 'reconciliations', element: wip('Сверки', undefined, '39:2 Акт сверки (Антиподсказчик)'), handle: { title: 'Сверки' } },
    { path: 'validation', element: wip('Валидация'), handle: { title: 'Валидация' } },
    { path: 'ai-stand', element: wip('ИИ-стенд'), handle: { title: 'ИИ-стенд' } },
  ],
}

export const router = createBrowserRouter([
  {
    element: <RootLayout />,
    HydrateFallback: FullPageLoading,
    errorElement: <RouteError />,
    children: [
      publicRoutes,
      authRoutes,
      candidateRoutes,
      employerRoutes,
      labRoutes,
      { path: 'dev/kit', lazy: async () => ({ Component: (await import('@/pages/dev/Kit')).default }), handle: { title: 'Витрина компонентов' } },
      { path: '*', element: <NotFound /> },
    ],
  },
])
