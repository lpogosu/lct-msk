import { useRouteError } from 'react-router-dom'
import { EmptyState, Button } from '@/ui'

export function NotFound() {
  return (
    <div className="mx-auto flex min-h-dvh max-w-[640px] items-center justify-center px-6">
      <main id="main">
        <EmptyState
          image="detector"
          title="Такой страницы нет"
          description="Возможно, ссылка устарела. Начните с главной — оттуда видно все разделы."
          action={<Button to="/">На главную</Button>}
        />
      </main>
    </div>
  )
}

/** Ошибка рендера маршрута: спокойное сообщение вместо белого экрана. */
export function RouteError() {
  const error = useRouteError()
  if (import.meta.env.DEV) console.error(error)
  return (
    <div className="mx-auto flex min-h-dvh max-w-[640px] items-center justify-center px-6">
      <main id="main">
        <EmptyState
          image="detector"
          title="Что-то пошло не так"
          description="Экран не открылся. Обновите страницу — если не поможет, вернитесь на главную."
          action={
            <Button variant="outline" to="/">
              На главную
            </Button>
          }
        />
      </main>
    </div>
  )
}
