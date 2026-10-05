import { useNavigate } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { ROLE_LABEL, useAuth } from '@/auth/useAuth'
import { formatDate } from '@/lib/format'
import { Button, MobileHeader, Panel, useToast } from '@/ui'

/**
 * Профиль кандидата — пока только аккаунт и выход. Резюме, приватность, поиск и журнал согласий (К13)
 * собирает экран-агент: заменить этот файл, сохранив кнопку «Выйти».
 */
export default function CandidateProfile() {
  const { user, role } = useAuth()
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
    <>
      <MobileHeader title="Профиль" />
      <h1 className="t-title mb-6 text-brand">Ваш аккаунт</h1>
      <Panel radius="md" padding="sm" className="flex flex-col gap-3">
        <div>
          <p className="t-small text-ink-3">Email</p>
          <p className="break-all t-label text-ink">{user?.email}</p>
        </div>
        <div className="flex gap-8">
          <div>
            <p className="t-small text-ink-3">Роль</p>
            <p className="t-label text-ink">{role ? ROLE_LABEL[role] : '—'}</p>
          </div>
          {user && (
            <div>
              <p className="t-small text-ink-3">Регистрация</p>
              <p className="t-label text-ink">{formatDate(user.created_at)}</p>
            </div>
          )}
        </div>
      </Panel>
      <p className="mt-4 t-small text-ink-3">
        Резюме, настройки приватности и журнал согласий появятся здесь. Контакты видны работодателю только после
        вашего «да».
      </p>
      <div className="mt-8">
        <Button variant="outline" block onClick={logout}>
          Выйти
        </Button>
      </div>
    </>
  )
}
