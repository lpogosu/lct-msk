import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage, toApiException } from '@/api/errors'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { SectionTitle, TraceList } from '@/features/candidate/components/Bits'
import { formatDate } from '@/lib/format'
import { qk } from '@/features/candidate/queries'
import { Alert, Button, MobileHeader, Panel, Segmented, useToast } from '@/ui'

type Variant = 'blind' | 'full'

/**
 * К8 «PDF-профиль»: слепая версия (псевдоним, без контактов — можно отправить самому) и полная (ФИО, контакты, точный балл).
 * Код сверки и ссылка /v/{код}: изменённый PDF сверку не пройдёт.
 */
export default function CandidateProfilePdf() {
  const [variant, setVariant] = useState<Variant>('blind')
  const toast = useToast()
  const info = useQuery({
    queryKey: qk.pdfInfo(variant),
    queryFn: () => unwrap(api.GET('/api/candidates/me/profile-pdf', { params: { query: { variant } } })),
    retry: false,
  })

  const download = useMutation({
    mutationFn: async () => {
      const res = await api.GET('/api/candidates/me/profile.pdf', { params: { query: { variant } }, parseAs: 'blob' })
      if (res.error !== undefined || !res.response.ok) throw toApiException(res.response.status, res.error)
      const blob = res.data as Blob
      const disp = res.response.headers.get('Content-Disposition') ?? ''
      const name = /filename="?([^";]+)"?/i.exec(disp)?.[1] ?? `profile-${variant}.pdf`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = name
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
      return res.response.headers.get('X-Verify-Code')
    },
    onSuccess: () => toast.success('PDF сохранён. Код сверки — на листе и в QR.'),
  })

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(new URL(url, window.location.origin).toString())
      toast.success('Ссылка скопирована.')
    } catch {
      toast.error('Не удалось скопировать — выделите ссылку вручную.')
    }
  }

  return (
    <>
      <MobileHeader title="PDF-профиль" backTo="/candidate/profile" />
      <div className="flex flex-col gap-7">
        <header>
          <h1 className="t-title text-brand">PDF-профиль</h1>
          <p className="mt-2 t-body text-ink-2">Стандартизированный лист: категория на линейке, модули теста, заявленное серым, ФСП и QR для сверки.</p>
        </header>

        <Segmented
          aria-label="Версия профиля"
          options={[
            { value: 'blind', label: 'Слепая' },
            { value: 'full', label: 'Полная' },
          ]}
          value={variant}
          onChange={setVariant}
        />

        <QueryBoundary query={info}>
          {(d) => (
            <>
              <p className="t-body text-ink-2">
                {d.variant === 'blind'
                  ? 'Псевдоним вместо имени, без контактов и компаний. Такую версию можно отправить самому.'
                  : 'ФИО, контакты, точный балл и выписка ФСП. Отправляйте только тем, кому доверяете.'}
              </p>

              <section>
                <SectionTitle as="h3">На листе</SectionTitle>
                <TraceList items={d.includes} />
              </section>
              {(d.hidden ?? []).length > 0 && (
                <section>
                  <SectionTitle as="h3">Скрыто в этой версии</SectionTitle>
                  <ul className="flex flex-wrap gap-1.5">
                    {d.hidden!.map((h) => (
                      <li key={h} className="rounded-pill bg-panel px-2.5 py-1 t-small text-ink-3">
                        {h}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-2">
                <p className="t-small text-ink-3">Код сверки · выдан {formatDate(d.issued_on)}</p>
                <p className="break-all font-mono text-[16px] font-medium text-ink">{d.code}</p>
                <p className="break-all t-small text-ink-2">{new URL(d.verify_url, window.location.origin).toString()}</p>
                <div>
                  <Button size="sm" variant="outline" onClick={() => void copy(d.verify_url)}>
                    Скопировать ссылку
                  </Button>
                </div>
              </Panel>

              {download.error ? <Alert tone="danger">{errorMessage(download.error)}</Alert> : null}
              <Button block size="lg" loading={download.isPending} onClick={() => download.mutate()}>
                Скачать PDF
              </Button>
              <p className="text-center t-small text-ink-3">Страница сверки показывает категорию и дату подтверждения; отредактированный PDF её не пройдёт.</p>
            </>
          )}
        </QueryBoundary>
      </div>
    </>
  )
}
