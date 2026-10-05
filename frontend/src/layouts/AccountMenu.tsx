import { useNavigate } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { Button } from '@/ui'
import { useToast } from '@/ui'

/** Содержимое меню аккаунта: кто вошёл и кнопка «Выйти». Используется в шапках работодателя и лаборатории. */
export function AccountMenu({ email }: { email: string }) {
  const navigate = useNavigate()
  const toast = useToast()

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      toast.error('Не удалось завершить сессию на сервере. Вы вышли на этом устройстве.')
    }
    navigate('/', { replace: true })
  }

  return (
    <div className="flex flex-col gap-1 p-1">
      <p className="px-2 pt-1 t-small text-ink-3">Вы вошли как</p>
      <p className="px-2 t-label text-ink [overflow-wrap:anywhere]">{email}</p>
      <div className="mt-2 border-t border-hairline px-2 pt-3 pb-1">
        <Button variant="outline" size="sm" block onClick={logout}>
          Выйти
        </Button>
      </div>
    </div>
  )
}
