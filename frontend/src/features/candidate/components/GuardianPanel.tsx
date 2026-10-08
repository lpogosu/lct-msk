import { useState, type FormEvent } from 'react'
import { errorMessage } from '@/api/errors'
import type { ProfileOut } from '@/features/candidate/queries'
import { useRequestGuardian } from '@/features/trust/api'
import { Alert, Button, Input, Panel, useToast } from '@/ui'

/**
 * Кандидату 15–17 лет нужно согласие законного представителя: до него профиль не виден работодателям. Представитель получает письмо
 * со ссылкой; после подтверждения работодатели могут предлагать только стажировку или лёгкий труд на неполный день.
 */
export function GuardianPanel({ profile }: { profile: ProfileOut }) {
  const g = profile.guardian
  const request = useRequestGuardian()
  const toast = useToast()
  const [name, setName] = useState(g?.guardian_name ?? '')
  const [email, setEmail] = useState('')
  if (!g || !g.required) return null

  const confirmed = g.state === 'confirmed'

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    try {
      await request.mutateAsync({ guardian_name: name.trim(), guardian_email: email.trim() })
      toast.success('Письмо отправлено представителю.')
    } catch {
      /* текст ошибки показывает request.error */
    }
  }

  return (
    <Panel variant={confirmed ? 'panel' : 'tint'} radius="xl" padding="md" aria-label="Согласие законного представителя" className="flex flex-col gap-4">
      <div>
        <h2 className="t-body-strong text-ink">Согласие законного представителя</h2>
        <p className="mt-1 t-body text-ink-2">{g.state_label}</p>
      </div>
      {confirmed ? (
        <p className="t-small text-ink-3">
          Представитель: {g.guardian_name} ({g.guardian_email_masked}). {g.employer_rule}.
        </p>
      ) : (
        <>
          <p className="t-small text-ink-2">
            Платформа доступна с 15 лет. Пока представитель не подтвердит согласие по ссылке из письма, работодатели вас не видят.
            {g.state === 'pending' && g.guardian_email_masked ? ` Письмо отправлено на ${g.guardian_email_masked}.` : ''}
          </p>
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
            <Input label="ФИО представителя" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
            <Input label="Email представителя" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" hint="Не ваш адрес: ссылка придёт на него." />
            {request.error ? <Alert tone="danger">{errorMessage(request.error)}</Alert> : null}
            <Button type="submit" block loading={request.isPending} disabled={name.trim().length < 5 || !email.includes('@')}>
              {g.state === 'pending' || g.state === 'expired' ? 'Отправить письмо ещё раз' : 'Отправить письмо представителю'}
            </Button>
          </form>
        </>
      )}
    </Panel>
  )
}
