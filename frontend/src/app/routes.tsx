import type { ComponentType } from 'react'
import { createBrowserRouter, Navigate, type RouteObject } from 'react-router-dom'
import { FullPageLoading, RedirectIfAuthed, RequireRole } from '@/auth/guards'
import { CandidateLayout } from '@/layouts/CandidateLayout'
import { EmployerLayout } from '@/layouts/EmployerLayout'
import { FspLayout } from '@/layouts/FspLayout'
import { LabLayout } from '@/layouts/LabLayout'
import { PublicLayout } from '@/layouts/PublicLayout'
import { RootLayout } from '@/layouts/RootLayout'
import { AuthLayout } from '@/pages/auth/AuthLayout'
import Landing from '@/pages/Landing'
import { NotFound, RouteError } from '@/pages/NotFound'

const publicRoutes: RouteObject = {
  element: <PublicLayout />,
  children: [
    { index: true, element: <Landing />, handle: { title: 'Главная' } },
    // Публичная сверка стандартизированного PDF-профиля по QR (ссылки /v/{code} печатает бэкенд)
    { path: 'v', lazy: async () => ({ Component: (await import('@/pages/Verify')).default }), handle: { title: 'Проверка документа' } },
    { path: 'v/:code', lazy: async () => ({ Component: (await import('@/pages/Verify')).default }), handle: { title: 'Сверка профиля' } },
    // Подтверждение согласия законного представителя по ссылке из письма (backend/app/mail/templates.py: guardian_confirm_link)
    {
      path: 'guardian-confirm',
      lazy: async () => ({ Component: (await import('@/pages/GuardianConfirm')).default }),
      handle: { title: 'Согласие представителя' },
    },
    // Вход для жюри: одноразовый кандидат, экспресс-тест, роли без пароля. Только DEMO_MODE (иначе страница сама скажет об этом)
    {
      path: 'jury',
      lazy: async () => ({ Component: (await import('@/pages/jury/JuryEntry')).default }),
      handle: { title: 'Для жюри' },
    },
    // Песочница жюри «Попробуй списать» (Антиподсказчик): без входа, работает только при DEMO_MODE (иначе страница сама скажет об этом)
    {
      path: 'try',
      lazy: async () => ({ Component: (await import('@/pages/jury/CheatLab')).default }),
      handle: { title: 'Попробуй списать' },
    },
  ],
}

// Экраны входа. Адреса /verify-email и /reset-password зашиты в ссылки из писем бэкенда
// (backend/app/mail/templates.py: ?email=…&code=…) — не переименовывать без правки бэкенда.
// Возврат из ФСП ID (адрес зашит в backend/app/api/fsp.py: FRONTEND_CALLBACK_PATH). Без RedirectIfAuthed: страница
// сама различает вход (нет сессии) и привязку (есть сессия) и сама ведёт дальше.
const fspCallbackRoute: RouteObject = {
  element: <AuthLayout />,
  children: [
    {
      path: 'candidate/fsp/callback',
      lazy: async () => ({ Component: (await import('@/pages/candidate/FspCallback')).default }),
      handle: { title: 'Вход через ФСП ID' },
    },
  ],
}

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
// Экраны кандидата — `src/pages/candidate/*`, их данные и мелкие компоненты — `src/features/candidate/*`.
const candidatePage = (load: () => Promise<{ default: ComponentType }>) => async () => ({ Component: (await load()).default })

const candidateRoutes: RouteObject = {
  path: 'candidate',
  element: (
    <RequireRole roles={['candidate']}>
      <CandidateLayout />
    </RequireRole>
  ),
  children: [
    { index: true, lazy: candidatePage(() => import('@/pages/candidate/Home')), handle: { title: 'Мой путь' } },
    { path: 'survey', lazy: candidatePage(() => import('@/pages/candidate/Survey')), handle: { title: 'Опрос' } },
    { path: 'grade', lazy: candidatePage(() => import('@/pages/candidate/Briefing')), handle: { title: 'Выбор грейда' } },
    { path: 'test/:sessionId', lazy: candidatePage(() => import('@/pages/candidate/Test')), handle: { title: 'Тест', hideNav: true } },
    { path: 'result/:sessionId', lazy: candidatePage(() => import('@/pages/candidate/Result')), handle: { title: 'Результат теста' } },
    { path: 'result/:sessionId/review', lazy: candidatePage(() => import('@/pages/candidate/Review')), handle: { title: 'Разбор ошибок' } },
    { path: 'invitations', lazy: candidatePage(() => import('@/pages/candidate/Invitations')), handle: { title: 'Приглашения' } },
    { path: 'invitations/:id', lazy: candidatePage(() => import('@/pages/candidate/Invitation')), handle: { title: 'Приглашение', hideNav: true } },
    { path: 'profile', lazy: candidatePage(() => import('@/pages/candidate/Profile')), handle: { title: 'Профиль' } },
    { path: 'profile/edit', lazy: candidatePage(() => import('@/pages/candidate/ProfileEdit')), handle: { title: 'Профиль и резюме' } },
    { path: 'profile/passport', lazy: candidatePage(() => import('@/pages/candidate/Passport')), handle: { title: 'Паспорт навыков' } },
    { path: 'profile/pdf', lazy: candidatePage(() => import('@/pages/candidate/ProfilePdf')), handle: { title: 'PDF-профиль' } },
    { path: 'qualification', lazy: candidatePage(() => import('@/pages/candidate/MyGrade')), handle: { title: 'Мой грейд' } },
    { path: 'fsp', lazy: candidatePage(() => import('@/pages/candidate/Fsp')), handle: { title: 'ФСП' } },
    { path: 'vacancies', lazy: candidatePage(() => import('@/pages/candidate/Vacancies')), handle: { title: 'Вакансии и отклики' } },
    { path: 'vacancies/:id', lazy: candidatePage(() => import('@/pages/candidate/VacancyDetail')), handle: { title: 'Вакансия' } },
    { path: 'tasks', lazy: candidatePage(() => import('@/pages/candidate/Tasks')), handle: { title: 'Задачи' } },
    { path: 'tasks/:assignmentId', lazy: candidatePage(() => import('@/pages/candidate/TaskPlayer')), handle: { title: 'Задача', hideNav: true } },
    { path: 'visibility', lazy: candidatePage(() => import('@/pages/candidate/Visibility')), handle: { title: 'Как меня видят' } },
    { path: 'privacy', lazy: candidatePage(() => import('@/pages/candidate/Privacy')), handle: { title: 'Приватность и поиск' } },
    { path: 'consents', lazy: candidatePage(() => import('@/pages/candidate/Consents')), handle: { title: 'Согласия' } },
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
    {
      path: 'company',
      lazy: async () => ({ Component: (await import('@/pages/employer/CompanyProfile')).default }),
      handle: { title: 'Профиль компании' },
    },
    {
      path: 'needs',
      lazy: async () => ({ Component: (await import('@/pages/employer/Needs')).default }),
      handle: { title: 'Потребности' },
    },
    {
      path: 'needs/new',
      lazy: async () => ({ Component: (await import('@/pages/employer/NeedBrief')).default }),
      handle: { title: 'Новая потребность' },
    },
    {
      path: 'needs/:id/edit',
      lazy: async () => ({ Component: (await import('@/pages/employer/NeedBrief')).default }),
      handle: { title: 'Правка потребности' },
    },
    {
      path: 'needs/:id',
      lazy: async () => ({ Component: (await import('@/pages/employer/NeedSelection')).default }),
      handle: { title: 'Подборка' },
    },
    {
      path: 'bank',
      lazy: async () => ({ Component: (await import('@/pages/employer/TalentMap')).default }),
      handle: { title: 'Карта талантов' },
    },
    {
      path: 'bank/list',
      lazy: async () => ({ Component: (await import('@/pages/employer/BankList')).default }),
      handle: { title: 'Банк кандидатов' },
    },
    {
      path: 'invitations',
      lazy: async () => ({ Component: (await import('@/pages/employer/Invitations')).default }),
      handle: { title: 'Приглашения' },
    },
    {
      path: 'vacancies',
      lazy: async () => ({ Component: (await import('@/pages/employer/Vacancies')).default }),
      handle: { title: 'Вакансии и отклики' },
    },
    {
      path: 'tasks',
      lazy: async () => ({ Component: (await import('@/pages/employer/Tasks')).default }),
      handle: { title: 'Задачи для кандидатов' },
    },
  ],
}

// Лаборатория (методист, жюри). Figma: Акт сверки (Антиподсказчик) 39:2. Экраны — `src/pages/lab/*`.
const labPage = (load: () => Promise<{ default: ComponentType }>) => async () => ({ Component: (await load()).default })

const labRoutes: RouteObject = {
  path: 'lab',
  element: (
    <RequireRole roles={['methodist', 'admin']}>
      <LabLayout />
    </RequireRole>
  ),
  children: [
    { index: true, element: <Navigate to="sessions" replace /> },
    { path: 'sessions', lazy: labPage(() => import('@/pages/lab/Sessions')), handle: { title: 'Сессии' } },
    { path: 'sessions/:id', lazy: labPage(() => import('@/pages/lab/SessionTrace')), handle: { title: 'Трасса сессии' } },
    { path: 'bank', lazy: labPage(() => import('@/pages/lab/Bank')), handle: { title: 'Банк заданий' } },
    { path: 'bank/:familyId', lazy: labPage(() => import('@/pages/lab/Clones')), handle: { title: '3 клона рядом' } },
    { path: 'quality', lazy: labPage(() => import('@/pages/lab/Quality')), handle: { title: 'Качество заданий' } },
    { path: 'reconciliations', lazy: labPage(() => import('@/pages/lab/Reconciliations')), handle: { title: 'Сверки' } },
    { path: 'reconciliations/:id', lazy: labPage(() => import('@/pages/lab/ReconciliationAct')), handle: { title: 'Акт сверки' } },
    { path: 'validation', lazy: labPage(() => import('@/pages/lab/Validation')), handle: { title: 'Валидация' } },
    { path: 'trust', lazy: labPage(() => import('@/pages/lab/Trust')), handle: { title: 'Жалобы и сигналы' } },
    { path: 'ai-stand', element: <Navigate to="/lab/sessions" replace /> },
  ],
}

// Панель ФСП «Кадровый резерв»: агрегаты с k-анонимностью. Роль — методист/админ (в демо вход с /jury, кнопка «Панель ФСП»).
const fspRoutes: RouteObject = {
  path: 'fsp',
  element: (
    <RequireRole roles={['methodist', 'admin']}>
      <FspLayout />
    </RequireRole>
  ),
  children: [
    { index: true, element: <Navigate to="reserve" replace /> },
    {
      path: 'reserve',
      lazy: async () => ({ Component: (await import('@/pages/fsp/Reserve')).default }),
      handle: { title: 'Кадровый резерв ФСП' },
    },
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
      fspCallbackRoute,
      candidateRoutes,
      employerRoutes,
      labRoutes,
      fspRoutes,
      { path: 'dev/kit', lazy: async () => ({ Component: (await import('@/pages/dev/Kit')).default }), handle: { title: 'Витрина компонентов' } },
      { path: '*', element: <NotFound /> },
    ],
  },
])
