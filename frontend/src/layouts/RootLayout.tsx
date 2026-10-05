import { useEffect } from 'react'
import { Outlet, ScrollRestoration, useMatches } from 'react-router-dom'

export interface RouteHandle {
  /** Название экрана для вкладки браузера */
  title?: string
  /** Скрыть нижнюю панель вкладок кандидата (тест, чтение результата…) */
  hideNav?: boolean
}

/** Общий корень: ссылка «К содержимому», заголовок вкладки по маршруту, восстановление прокрутки. */
export function RootLayout() {
  const matches = useMatches()
  const title = [...matches].reverse().map((m) => (m.handle as RouteHandle | undefined)?.title).find(Boolean)

  useEffect(() => {
    document.title = title ? `${title} · Взаимно` : 'Взаимно — подбор ИТ-специалистов по подтверждённому уровню'
  }, [title])

  return (
    <>
      <a href="#main" className="skip-link t-label">
        К содержимому
      </a>
      <Outlet />
      <ScrollRestoration />
    </>
  )
}
