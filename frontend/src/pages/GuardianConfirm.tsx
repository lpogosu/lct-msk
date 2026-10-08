import { useMutation } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { Alert, Button, Panel } from '@/ui'

/** Публичный экран по ссылке из письма законному представителю кандидата 15–17 лет (без входа). */
export default function GuardianConfirm() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const confirm = useMutation({ mutationFn: () => unwrap(api.POST('/api/guardian/confirm', { body: { token } })) })

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6 px-4 py-12">
      <h1 className="t-title text-brand">Согласие законного представителя</h1>
      {!token ? (
        <Alert tone="danger">В ссылке нет токена. Откройте её из письма целиком или попросите отправить письмо ещё раз.</Alert>
      ) : confirm.isSuccess ? (
        <Alert tone="success" title="Согласие записано">
          {confirm.data.message}
        </Alert>
      ) : (
        <Panel variant="tint" radius="xl" padding="md" className="flex flex-col gap-4">
          <p className="t-body text-ink-2">
            Пользователю от 15 до 17 лет на платформе «Взаимно» нужно согласие законного представителя. Подтверждая, вы разрешаете обработку его
            персональных данных на платформе и показ его слепой карточки работодателям.
          </p>
          <ul className="m-0 list-disc pl-5 t-body text-ink-2">
            <li>Работодатели смогут предлагать только стажировку или лёгкий труд на неполный день.</li>
            <li>Контакты раскрываются работодателю только после взаимного согласия самого пользователя.</li>
            <li>Отозвать согласие можно, обратившись в поддержку или удалив аккаунт.</li>
          </ul>
          {confirm.error ? <Alert tone="danger">{errorMessage(confirm.error)}</Alert> : null}
          <Button size="lg" block loading={confirm.isPending} onClick={() => confirm.mutate()}>
            Подтверждаю согласие
          </Button>
          <p className="t-small text-ink-3">Если вы не знаете этого пользователя или не согласны, просто закройте страницу: профиль останется скрытым.</p>
        </Panel>
      )}
    </div>
  )
}
