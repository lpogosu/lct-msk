import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, unwrap } from '@/api/client'
import { Alert, Button, Panel, Skeleton, TrustNode } from '@/ui'

const fmt = (d?: string | null) => (d ? new Date(d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : '—')

/** Публичная сверка PDF-профиля по коду из QR (концепция §3.1.2): без входа, без ПДн. */
export default function VerifyProfile() {
  const { code = '' } = useParams()
  const q = useQuery({
    queryKey: ['verify', code],
    queryFn: () => unwrap(api.GET('/api/v/{code}', { params: { path: { code } } })),
    retry: false,
  })
  const v = q.data
  return (
    <div className="mx-auto flex max-w-[640px] flex-col gap-6 px-4 py-8 sm:px-6">
      <header>
        <p className="t-small text-ink-3">Сверка PDF-профиля</p>
        <h1 className="t-title text-brand">Выдан ли этот документ платформой?</h1>
      </header>
      {q.isLoading && <Skeleton className="h-40 w-full" />}
      {q.isError && <Alert tone="danger">Не удалось выполнить сверку. Проверьте ссылку и попробуйте ещё раз.</Alert>}
      {v && !v.valid && (
        <Panel padding="lg" className="flex flex-col gap-3">
          <h2 className="t-body-strong text-ink">Документ не найден</h2>
          <p className="t-body text-ink-2">{v.message} Платформа такого PDF не выдавала либо он был изменён.</p>
        </Panel>
      )}
      {v && v.valid && (
        <Panel padding="lg" className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <TrustNode kind="test" />
            <h2 className="t-body-strong text-ink">{v.message}</h2>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 t-body">
            <dt className="text-ink-3">Категория</dt>
            <dd className="text-ink">{v.category?.name ?? '—'}</dd>
            <dt className="text-ink-3">Версия</dt>
            <dd className="text-ink">{v.variant_label ?? '—'}</dd>
            <dt className="text-ink-3">Подтверждено тестом</dt>
            <dd className="text-ink">{fmt(v.confirmed_on)}</dd>
            <dt className="text-ink-3">Выдан</dt>
            <dd className="text-ink">{fmt(v.issued_on)}</dd>
            {v.interval && (
              <>
                <dt className="text-ink-3">Интервал на линейке</dt>
                <dd className="text-ink">
                  {v.interval[0]}–{v.interval[1]}
                </dd>
              </>
            )}
          </dl>
          {v.actual_note && <p className="t-small text-ink-2">{v.actual_note}</p>}
        </Panel>
      )}
      <Button variant="text" to="/">
        На главную
      </Button>
    </div>
  )
}
