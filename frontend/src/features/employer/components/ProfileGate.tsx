import { Alert, Button } from '@/ui'
import { useCompany } from '../api'

/** Плашка «заполните профиль»: без профиля компании приглашения не отправляются (концепция §3.2, шаг 2). */
export function ProfileGate({ className }: { className?: string }) {
  const company = useCompany()
  if (!company.data || company.data.profile_complete) return null
  return (
    <div className={className}>
      <Alert tone="info" title="Заполните профиль компании, чтобы приглашать">
        <p>Без него приглашения не отправляются: кандидат должен видеть, кто ему пишет. Не хватает: {company.data.missing.join(', ')}.</p>
        <div className="mt-3">
          <Button size="sm" to="/employer/company">
            Заполнить профиль
          </Button>
        </div>
      </Alert>
    </div>
  )
}
