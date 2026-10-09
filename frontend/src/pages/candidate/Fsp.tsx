import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { Chip, SectionTitle, Toggle } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { Sheet } from '@/features/candidate/components/Sheet'
import { qk, useFsp, type FspStatus } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { Alert, Button, Illustration, MobileHeader, Panel, Skeleton, TrustNode, useToast } from '@/ui'

type Result = Schemas['FspResultOut']

/**
 * К14 «ФСП»: привязка через ФСП ID (OIDC) или «демо-вход ФСП», результаты с переключателями «показывать»,
 * выписка, обновление и отвязка. ФСП не влияет на грейд, а отсутствие ФСП — не штраф.
 */
export default function CandidateFsp() {
  const q = useFsp()
  return (
    <>
      <MobileHeader title="ФСП" backTo="/candidate/profile" />
      <div className="flex flex-col gap-8">
        <header>
          <h1 className="t-title text-brand">ФСП ID и достижения</h1>
          <p className="mt-2 t-body text-ink-2">
            Достижения из реестра ФСП усиливают профиль, но не меняют грейд. Без них вы проходите тот же путь — это не штраф.
          </p>
        </header>
        <QueryBoundary query={q}>{(s) => (s.linked ? <Linked s={s} /> : <NotLinked s={s} />)}</QueryBoundary>
      </div>
    </>
  )
}

// --- Не привязан ----------------------------------------------------------------------------------------------------------

function NotLinked({ s }: { s: FspStatus }) {
  const qc = useQueryClient()
  const toast = useToast()
  const avail = useQuery({ queryKey: qk.fspAvailability, queryFn: () => unwrap(api.GET('/api/fsp/availability')), retry: false })
  const [pickDemo, setPickDemo] = useState(false)

  const startLogin = useMutation({
    mutationFn: () => unwrap(api.GET('/api/fsp/login', { params: { query: { mode: 'link' } } })),
    onSuccess: (r) => {
      window.location.href = r.authorization_url
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  return (
    <div className="flex flex-col gap-6">
      <Panel variant="hero" radius="hero" padding="md" className="flex flex-col items-center gap-4 text-center">
        <Illustration name="medal_fsp" className="w-40" />
        <p className="t-name text-ink">{s.status_line}</p>
        <p className="t-small text-ink-2">{s.employer_line}</p>
      </Panel>

      {avail.isPending ? (
        <Skeleton className="h-12 w-full rounded-pill" />
      ) : (
        <div className="flex flex-col gap-3">
          {avail.data?.fsp_id_available && (
            <Button block size="lg" loading={startLogin.isPending} onClick={() => startLogin.mutate()}>
              Войти через ФСП ID
            </Button>
          )}
          {avail.data?.demo_login_available && (
            <>
              {!avail.data.fsp_id_available && (
                <Alert>ФСП ID сейчас недоступен, но на стенде работает «демо-вход ФСП»: вымышленные спортсмены демо-реестра.</Alert>
              )}
              <Button block size="lg" variant={avail.data.fsp_id_available ? 'outline' : 'primary'} onClick={() => setPickDemo(true)}>
                Демо-вход ФСП
              </Button>
            </>
          )}
          {avail.data && !avail.data.fsp_id_available && !avail.data.demo_login_available && (
            <Alert tone="danger" title="ФСП ID недоступен">
              Привязать достижения сейчас нельзя. Попробуйте позже — остальной путь от этого не зависит.
            </Alert>
          )}
          {avail.isError && <Alert tone="danger">{errorMessage(avail.error)}</Alert>}
        </div>
      )}
      <p className="t-small text-ink-3">{s.note}</p>

      <DemoSheet
        open={pickDemo}
        onClose={() => setPickDemo(false)}
        onLinked={() => {
          void qc.invalidateQueries({ queryKey: qk.fsp })
          setPickDemo(false)
        }}
      />
    </div>
  )
}

function DemoSheet({ open, onClose, onLinked }: { open: boolean; onClose: () => void; onLinked: () => void }) {
  const toast = useToast()
  const athletes = useQuery({
    queryKey: ['candidate', 'fsp-demo-athletes'],
    queryFn: () => unwrap(api.GET('/api/fsp/demo/athletes')),
    enabled: open,
    retry: false,
  })
  const link = useMutation({
    mutationFn: (sub: string) => unwrap(api.POST('/api/fsp/demo-login', { body: { sub, mode: 'link', consent_personal_data: false, consent_fsp_enrichment: false } })),
    onSuccess: () => {
      toast.success('ФСП ID привязан (демо). Данные реестра пока только показаны — сохранить их можно с вашего согласия.')
      onLinked()
    },
  })
  return (
    <Sheet open={open} onClose={onClose} title="Демо-вход ФСП" description="Выберите вымышленного спортсмена из демо-реестра. Реальных ФИО здесь нет.">
      <div className="flex flex-col gap-3">
        {athletes.isPending && <Skeleton className="h-24 w-full rounded-panel-lg" />}
        {athletes.isError && <Alert tone="danger">{errorMessage(athletes.error)}</Alert>}
        {athletes.data?.map((a) => (
          <button
            key={a.sub}
            type="button"
            disabled={link.isPending}
            onClick={() => link.mutate(a.sub)}
            className="flex flex-col gap-0.5 rounded-panel-lg border border-hairline bg-white px-4 py-3 text-left hover:border-brand disabled:opacity-60"
          >
            <span className="t-label text-ink">{a.label}</span>
            <span className="t-small text-ink-3">{a.summary}</span>
          </button>
        ))}
        {link.error ? <Alert tone="danger">{errorMessage(link.error)}</Alert> : null}
      </div>
    </Sheet>
  )
}

// --- Привязан -----------------------------------------------------------------------------------------------------------

function Linked({ s }: { s: FspStatus }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [unlinkOpen, setUnlinkOpen] = useState(false)
  const results = s.results ?? []
  const badges = s.badges ?? []

  const setStatus = (d: FspStatus) => qc.setQueryData(qk.fsp, d)

  const enrich = useMutation({
    mutationFn: () => unwrap(api.POST('/api/fsp/me/enrichment', { body: { text_version: s.consent_text_version } })),
    onSuccess: (d) => {
      setStatus(d)
      void qc.invalidateQueries({ queryKey: qk.consents })
      toast.success('Согласие записано, данные ФСП сохранены в профиле.')
    },
  })
  const refresh = useMutation({
    mutationFn: () => unwrap(api.POST('/api/fsp/me/refresh')),
    onSuccess: (d) => {
      setStatus(d)
      toast.success('Данные из реестра обновлены.')
    },
  })
  const unlink = useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/fsp/me')),
    onSuccess: (d) => {
      setStatus(d)
      void qc.invalidateQueries({ queryKey: qk.consents })
      void qc.invalidateQueries({ queryKey: qk.privacy })
      toast.success('ФСП ID отвязан, данные реестра удалены. Подбор пересчитает оценку без ФСП — это не штраф.')
      setUnlinkOpen(false)
    },
  })

  return (
    <div className="flex flex-col gap-7">
      <Panel variant="hero" radius="hero" padding="md" className="flex flex-col gap-2">
        <p className="t-name text-ink">{s.status_line}</p>
        <p className="t-small text-ink-2">
          {[s.is_demo ? 'Демо-вход ФСП' : null, s.source_label, s.data_ago ? `данные реестра: ${s.data_ago}` : null].filter(Boolean).join(' · ')}
        </p>
        {badges.length > 0 && (
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {badges.map((b) => (
              <li key={b}>
                <Chip tone="tint">
                  <TrustNode kind="fsp" surface="#F3F1FF" />
                  {b}
                </Chip>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {s.preview_only && (
        <Panel variant="tint" radius="lg" padding="md" className="flex flex-col gap-3">
          <p className="t-name text-ink">Данные пока только показаны</p>
          <p className="t-body text-ink-2">
            Без вашего согласия мы не сохраняем данные реестра и не используем их в подборе. Разрешить обогащение профиля — отдельное согласие, его можно отозвать.
          </p>
          <Button loading={enrich.isPending} onClick={() => enrich.mutate()}>
            Разрешить использовать данные ФСП
          </Button>
          {enrich.error ? <Alert tone="danger">{errorMessage(enrich.error)}</Alert> : null}
        </Panel>
      )}
      {s.enrichment_requested && !s.consent_fsp_enrichment && (
        <Alert>Вы отметили согласие при входе: оно применится после подтверждения email или кнопкой выше.</Alert>
      )}
      {s.sync_error && (
        <Alert tone="danger" title="Не удалось обновить данные">
          {s.sync_error}
        </Alert>
      )}

      <section aria-labelledby="fsp-res-h">
        <SectionTitle right={s.pending_count > 0 ? `ждут протокола: ${s.pending_count}` : undefined}>
          <span id="fsp-res-h">Результаты</span>
        </SectionTitle>
        {results.length === 0 ? (
          <p className="t-body text-ink-3">В реестре пока нет результатов. Это не штраф: оценка идёт по тесту и задачам.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {results.map((r) => (
              <li key={r.id}>
                <ResultCard r={r} onStatus={setStatus} />
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 t-small text-ink-3">
          Скрытый результат не участвует в оценке и не виден работодателям: иначе была бы скрытая накрутка.
        </p>
      </section>

      <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-1">
        <p className="t-label text-ink">Так вас видит работодатель</p>
        <p className="t-small text-ink-2">{s.employer_line}</p>
        <p className="t-small text-ink-3">Номер ЕКП, команда и протокол — только после взаимного «да».</p>
      </Panel>

      {s.extract && (
        <section>
          <SectionTitle as="h3">Выписка из протокола</SectionTitle>
          <pre className="m-0 overflow-x-auto rounded-panel-lg bg-code-bg p-4 font-mono text-[12px] leading-[18px] text-white">{s.extract}</pre>
          <p className="mt-1.5 t-small text-ink-3">Так её увидит компания после раскрытия контактов.</p>
        </section>
      )}

      <div className="flex flex-col items-start gap-3">
        <Button variant="outline" loading={refresh.isPending} disabled={s.preview_only} onClick={() => refresh.mutate()}>
          Обновить из реестра
        </Button>
        {refresh.error ? <Alert tone="danger">{errorMessage(refresh.error)}</Alert> : null}
        <Button variant="text" className="text-ink-2" onClick={() => setUnlinkOpen(true)}>
          Отвязать ФСП ID
        </Button>
      </div>

      <Sheet
        open={unlinkOpen}
        onClose={() => setUnlinkOpen(false)}
        title="Отвязать ФСП ID?"
        description="Удалим привязку и сохранённые данные реестра, согласие на обогащение отзовём (запись останется в журнале). Подбор пересчитает оценку без ФСП — это не штраф."
      >
        <div className="flex flex-col gap-3">
          {unlink.error ? <Alert tone="danger">{errorMessage(unlink.error)}</Alert> : null}
          <Button block variant="outline" loading={unlink.isPending} onClick={() => unlink.mutate()}>
            Отвязать
          </Button>
          <Button block variant="text" onClick={() => setUnlinkOpen(false)}>
            Оставить
          </Button>
        </div>
      </Sheet>
    </div>
  )
}

function ResultCard({ r, onStatus }: { r: Result; onStatus: (d: FspStatus) => void }) {
  const toast = useToast()
  const hide = useMutation({
    mutationFn: (hidden: boolean) =>
      unwrap(api.PATCH('/api/fsp/me/results/{result_id}', { params: { path: { result_id: r.id } }, body: { hidden } })),
    onSuccess: onStatus,
    onError: (e) => toast.error(errorMessage(e)),
  })
  return (
    <Panel variant="outline" radius="lg" padding="sm" className={cn('flex flex-col gap-2', r.hidden && 'opacity-70')}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="t-body-strong text-ink">{r.title}</p>
          <p className="t-small text-ink-2">
            {r.discipline} · {r.level} · {r.year}
          </p>
        </div>
        <Chip tone={r.counted ? 'tint' : 'panel'}>{r.status_label}</Chip>
      </div>
      <p className="t-small text-ink-2">
        {r.outcome}
        {r.role ? ` · ${r.role}` : ''}
        <span className="text-ink-3"> · место: {r.place} (видите только вы)</span>
      </p>
      {r.declared_role && (
        <p className="t-small text-ink-3">
          {r.declared_role}: в протоколе ФСП роли в команде нет, поэтому на оценку она не влияет
        </p>
      )}
      {r.employer_text && !r.hidden && <p className="t-small text-ink-3">Работодатель увидит: {r.employer_text}</p>}
      <div className="flex items-center justify-between gap-3 border-t border-hairline pt-2">
        <span className="t-small text-ink-2">{r.hidden ? 'Скрыт: не учитывается' : 'Показывать работодателю'}</span>
        <Toggle checked={!r.hidden} disabled={hide.isPending} label={`Показывать результат «${r.title}»`} onChange={(show) => hide.mutate(!show)} />
      </div>
    </Panel>
  )
}
