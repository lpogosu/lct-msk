import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { Chip, SectionTitle, TraceList } from '@/features/candidate/components/Bits'
import { DisclosureFields } from '@/features/candidate/components/DisclosureFields'
import { chosen, fieldOptions, hasContact } from '@/features/candidate/components/disclosureUtil'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { Sheet } from '@/features/candidate/components/Sheet'
import { cityLabel, salaryHero } from '@/features/candidate/format'
import { qk, usePrivacy } from '@/features/candidate/queries'
import { Alert, Button, Checkbox, MobileHeader, Panel, ScoreRing, Skeleton, useToast } from '@/ui'

type Detail = Schemas['VacancyCandidateDetail']

const PART_LABELS: Record<string, string> = { T: 'тест', K: 'стек', F: 'ФСП', A: 'свежесть', U: 'условия' }

type Check = {
  note?: string
  covered?: { name: string; text: string }[]
  untestable?: { name: string; text: string }[]
}

/** Карточка вакансии (К12): вилка, компания и надёжность, ваш Score, «как мы проверим» и отклик с отдельным согласием. */
export default function CandidateVacancyDetail() {
  const { id = '' } = useParams()
  const q = useQuery({
    queryKey: qk.vacancy(id),
    queryFn: () => unwrap(api.GET('/api/vacancies/{vacancy_id}', { params: { path: { vacancy_id: id } } })),
    retry: false,
  })
  return (
    <>
      <MobileHeader title="Вакансия" backTo="/candidate/vacancies" />
      <QueryBoundary query={q} errorTitle="Не удалось открыть вакансию">
        {(v) => <View v={v} />}
      </QueryBoundary>
    </>
  )
}

function View({ v }: { v: Detail }) {
  const [open, setOpen] = useState(false)
  const { range, unit } = salaryHero(v.salary.salary_from, v.salary.salary_to)
  const check = (v.how_we_check ?? null) as Check | null
  const parts = Object.entries(v.parts ?? {}).filter(([, n]) => n > 0)
  const applied = !!v.application_status

  return (
    <div className="flex flex-col gap-7">
      {v.company && (
        <div>
          <h2 className="t-name text-ink">{v.company.name}</h2>
          <p className="mt-0.5 t-small text-ink-2">{[v.company.field, v.company.reliability.display].filter(Boolean).join('. ')}</p>
        </div>
      )}

      <section aria-label="Вилка">
        <p className="font-mono text-[44px] font-medium leading-[52px] tracking-[-1.5px] text-brand max-[380px]:text-[36px]">{range}</p>
        <p className="mt-1 t-lead text-ink">
          {unit} {v.salary.salary_type_label} в месяц
        </p>
      </section>

      <section>
        <h1 className="text-[20px] font-semibold leading-[26px] text-ink">{v.title}</h1>
        <p className="mt-2 whitespace-pre-line t-lead text-ink-2">{v.description}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {v.format_label && <Chip tone="panel">{v.format_label}</Chip>}
          {v.city && <Chip tone="panel">{cityLabel(v.city)}</Chip>}
          {v.published_text && <Chip tone="panel">опубликовано {v.published_text}</Chip>}
        </div>
      </section>

      {v.score != null && (
        <section className="flex items-center gap-4" aria-label="Ваше соответствие">
          <ScoreRing value={Math.round(v.score)} />
          <div className="min-w-0">
            <p className="t-label text-ink">Ваше соответствие вакансии</p>
            <p className="mt-0.5 t-small text-ink-3">Тот же Score, что видит работодатель. Резюме и самоописание в него не входят.</p>
            {parts.length > 0 && (
              <p className="mt-1 t-small text-ink-2">{parts.map(([k, n]) => `${PART_LABELS[k] ?? k} ${Math.round(n)}`).join(' · ')}</p>
            )}
          </div>
        </section>
      )}
      {v.score == null && (
        <Alert>
          Без подтверждённой категории Score не считается.{' '}
          <Link to="/candidate" className="text-brand underline-offset-4 hover:underline">
            Пройти тест
          </Link>
        </Alert>
      )}

      {check && (check.covered?.length || check.untestable?.length) ? (
        <section>
          <SectionTitle as="h3">Как мы проверим</SectionTitle>
          {check.note && <p className="mb-3 t-small text-ink-3">{check.note}</p>}
          <TraceList items={[...(check.covered ?? []).map((c) => c.text), ...(check.untestable ?? []).map((c) => c.text)]} />
        </section>
      ) : null}

      <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-3">
        {applied ? (
          <p className="t-body text-ink">Вы откликнулись на эту вакансию. Статус — в разделе «Мои отклики».</p>
        ) : (
          <>
            <p className="t-small text-ink-2">
              Компания ответит за {v.response_days} дней. Контакты она увидит сразу после отклика — только с вашего отдельного согласия.
            </p>
            <Button block size="lg" onClick={() => setOpen(true)}>
              Откликнуться
            </Button>
          </>
        )}
        {applied && (
          <Button block variant="outline" to="/candidate/vacancies">
            К списку вакансий
          </Button>
        )}
      </Panel>

      <ApplySheet open={open} onClose={() => setOpen(false)} v={v} />
    </div>
  )
}

function ApplySheet({ open, onClose, v }: { open: boolean; onClose: () => void; v: Detail }) {
  const privacy = usePrivacy()
  return (
    <Sheet open={open} onClose={onClose} title={`Откликнуться: передать контакты «${v.company?.name ?? 'компании'}»?`} description={String(v.consent.text ?? '')}>
      {privacy.isPending ? <Skeleton className="h-40 w-full rounded-panel" /> : <ApplyForm v={v} onClose={onClose} privacy={privacy.data} />}
    </Sheet>
  )
}

function ApplyForm({ v, onClose, privacy }: { v: Detail; onClose: () => void; privacy: ReturnType<typeof usePrivacy>['data'] }) {
  const qc = useQueryClient()
  const toast = useToast()
  const defaults = Array.isArray(v.consent.default_fields) ? (v.consent.default_fields as string[]) : []
  const { options, initial } = fieldOptions(privacy, defaults)
  const [fields, setFields] = useState(initial)
  const [agree, setAgree] = useState(false)
  const contactOk = hasContact(fields)

  const apply = useMutation({
    mutationFn: () => unwrap(api.POST('/api/applications', { body: { vacancy_id: v.id, consent: true, fields: chosen(fields) } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.vacancy(v.id) })
      void qc.invalidateQueries({ queryKey: ['candidate', 'vacancies'] })
      void qc.invalidateQueries({ queryKey: qk.applications })
      void qc.invalidateQueries({ queryKey: qk.consents })
      toast.success('Отклик отправлен. Компания ответит в срок.')
      onClose()
    },
  })

  return (
    <div className="flex flex-col gap-5">
      <DisclosureFields options={options} value={fields} onChange={setFields} />
      <Checkbox
        checked={agree}
        onChange={(e) => setAgree(e.target.checked)}
        label={<span className="t-body-strong">Согласен передать выбранные данные компании «{v.company?.name ?? 'компании'}»</span>}
        description={`Отдельное согласие на обработку персональных данных (версия текста ${String(v.consent.text_version ?? '')}). Его можно отозвать в журнале согласий.`}
      />
      {!contactOk && <Alert tone="danger">Выберите хотя бы один способ связи.</Alert>}
      {apply.error ? <Alert tone="danger">{errorMessage(apply.error)}</Alert> : null}
      <Button block size="lg" disabled={!agree || !contactOk} loading={apply.isPending} onClick={() => apply.mutate()}>
        Откликнуться
      </Button>
    </div>
  )
}
