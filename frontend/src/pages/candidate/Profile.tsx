import { useNavigate } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { ROLE_LABEL, useAuth } from '@/auth/useAuth'
import { Chip, Fact, RowLink } from '@/features/candidate/components/Bits'
import { mainQualification, useFsp, useInbox, useProfile, useQualifications, useTasks } from '@/features/candidate/queries'
import { formatDate } from '@/lib/format'
import { Button, Panel, useToast } from '@/ui'

/**
 * Профиль кандидата — центр разделов: грейд, резюме и профиль, PDF, ФСП, приватность и поиск, вакансии, задачи,
 * согласия. Аккаунт и выход — внизу.
 */
export default function CandidateProfile() {
  const { user, role } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const profile = useProfile()
  const quals = useQualifications()
  const fsp = useFsp()
  const tasks = useTasks()
  const inbox = useInbox()

  const main = quals.data ? mainQualification(quals.data) : null
  const missing = profile.data?.missing ?? []
  const activeTasks = tasks.data?.active.length ?? 0

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      toast.error('Не удалось завершить сессию на сервере. Вы вышли на этом устройстве.')
    }
    navigate('/', { replace: true })
  }

  return (
    <div className="flex flex-col gap-8">
      <header>
        <h1 className="t-title text-brand">Профиль</h1>
        <p className="mt-2 t-body text-ink-2">
          Всё, что вы пишете о себе, — «заявлено»: на категорию и место в подборке не влияет. Контакты видны работодателю только после вашего «да».
        </p>
      </header>

      <nav aria-label="Разделы профиля">
        <ul className="m-0 flex list-none flex-col p-0">
          <li>
            <RowLink
              to="/candidate/qualification"
              title="Мой грейд"
              detail={main?.category ? `${main.category.label} · ${main.status_ru}` : (main?.status_ru ?? 'Категория появится после теста')}
            />
          </li>
          <li>
            <RowLink
              to="/candidate/profile/edit"
              title="Профиль и резюме"
              detail={missing.length > 0 ? `Можно дополнить: ${missing.length}` : 'Заполнен'}
            />
          </li>
          <li>
            <RowLink to="/candidate/profile/pdf" title="PDF-профиль" detail="Слепая и полная версия, QR для сверки" />
          </li>
          <li>
            <RowLink
              to="/candidate/fsp"
              title="ФСП ID и достижения"
              detail={fsp.data ? fsp.data.status_line : 'Привязка не обязательна'}
            />
          </li>
          <li>
            <RowLink to="/candidate/privacy" title="Приватность и поиск" detail="Режим поиска, минимум зарплаты, как вас видят" />
          </li>
          <li>
            <RowLink
              to="/candidate/vacancies"
              title="Вакансии и отклики"
              detail="Вакансии по вашей категории, ваши отклики"
            />
          </li>
          <li>
            <RowLink
              to="/candidate/tasks"
              title="Задачи"
              detail="Короткие задания: свежесть профиля и подтверждение модулей"
              badge={activeTasks > 0 ? <Chip tone="brand">{activeTasks}</Chip> : undefined}
            />
          </li>
          <li>
            <RowLink
              to="/candidate/invitations"
              title="Приглашения"
              detail="Ответы «Да / Да, если… / Нет»"
              badge={inbox.data && inbox.data.new_count > 0 ? <Chip tone="brand">{inbox.data.new_count}</Chip> : undefined}
            />
          </li>
          <li>
            <RowLink to="/candidate/visibility" title="Как меня видят" detail="Показы в подборках за 7 дней" />
          </li>
          <li>
            <RowLink to="/candidate/consents" title="Согласия" detail="Журнал и отзыв согласий" />
          </li>
        </ul>
      </nav>

      <Panel radius="md" padding="sm" className="flex flex-col gap-3">
        <Fact label="Email">
          <span className="break-all">{user?.email}</span>
        </Fact>
        <div className="flex gap-8">
          <Fact label="Роль">{role ? ROLE_LABEL[role] : '—'}</Fact>
          {user && <Fact label="Регистрация">{formatDate(user.created_at)}</Fact>}
        </div>
      </Panel>

      <Button variant="outline" block onClick={logout}>
        Выйти
      </Button>
    </div>
  )
}
