import { Link, Outlet } from 'react-router-dom'
import { homePathForRole, useAuth } from '@/auth/useAuth'
import { Button, Logo } from '@/ui'

/** Публичная рамка: логотип, вход, подвал. Главная и справочные страницы. */
export function PublicLayout() {
  const { isAuthenticated, role } = useAuth()
  return (
    <div className="flex min-h-dvh flex-col bg-white">
      <header className="border-b border-hairline">
        <div className="mx-auto flex max-w-page items-center justify-between gap-4 px-4 py-3.5 sm:px-6 lg:px-12 lg:py-5">
          <Logo />
          <nav aria-label="Вход" className="flex items-center gap-2 sm:gap-3">
            {isAuthenticated ? (
              <Button size="sm" to={homePathForRole(role)}>
                В кабинет
              </Button>
            ) : (
              <>
                <Button variant="text" to="/auth/login" className="px-2">
                  Войти
                </Button>
                <Button size="sm" variant="outline" to="/auth/register" className="hidden sm:inline-flex">
                  Регистрация
                </Button>
              </>
            )}
          </nav>
        </div>
      </header>

      <main id="main" className="flex-1">
        <Outlet />
      </main>

      <footer className="border-t border-hairline">
        <div className="mx-auto flex max-w-page flex-col gap-2 px-4 py-6 t-small text-ink-3 sm:px-6 lg:flex-row lg:justify-between lg:px-12">
          <p>«Взаимно» — обратный подбор ИТ-специалистов по подтверждённому уровню.</p>
          <p>
            Контакты кандидата раскрываются работодателю только после взаимного «да». Данные хранятся в РФ.{' '}
            <Link to="/auth/login" className="text-ink-2 underline underline-offset-4">
              Войти
            </Link>
          </p>
        </div>
      </footer>
    </div>
  )
}
