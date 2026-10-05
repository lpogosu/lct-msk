import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { Chip } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { Sheet } from '@/features/candidate/components/Sheet'
import { cityLabel, salaryLine } from '@/features/candidate/format'
import { qk } from '@/features/candidate/queries'
import { Alert, Button, Checkbox, EmptyState, Segmented, useToast } from '@/ui'

type VacancyItem = Schemas['VacancyCandidateItem']
type Application = Schemas['ApplicationCandidateCard']

type Tab = 'vacancies' | 'applications'

/** К12 «Вакансии и отклики»: список по тому же Score, что видит работодатель, и мои отклики со статусами. */
export default function CandidateVacancies() {
  const [tab, setTab] = useState<Tab>('vacancies')
  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="t-title text-brand">Вакансии и отклики</h1>
        <p className="mt-2 t-body text-ink-2">
          Список отсортирован по тому же Score, что видит работодатель. Откликаясь, вы отдельно соглашаетесь передать компании контакты.
        </p>
      </header>
      <Segmented
        aria-label="Раздел"
        options={[
          { value: 'vacancies', label: 'Вакансии' },
          { value: 'applications', label: 'Мои отклики' },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'vacancies' ? <VacancyList /> : <MyApplications />}
    </div>
  )
}

function VacancyList() {
  const [only, setOnly] = useState(true)
  const q = useQuery({
    queryKey: qk.vacancies(only),
    queryFn: () => unwrap(api.GET('/api/vacancies', { params: { query: { only_matching: only } } })),
  })
  return (
    <div className="flex flex-col gap-4">
      <Checkbox
        checked={only}
        onChange={(e) => setOnly(e.target.checked)}
        label={<span className="t-body">Только по моей категории</span>}
      />
      <QueryBoundary query={q}>
        {(list) =>
          list.length === 0 ? (
            <EmptyState
              size="compact"
              title={only ? 'Нет вакансий по вашей категории' : 'Вакансий пока нет'}
              description={
                only
                  ? 'Покажите все вакансии или подтвердите категорию тестом — тогда подборка станет точнее.'
                  : 'Компании ещё не опубликовали вакансии. Приглашения приходят и без отклика.'
              }
              action={
                only ? (
                  <Button variant="outline" onClick={() => setOnly(false)}>
                    Показать все
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <ul className="flex flex-col gap-3">
              {list.map((v) => (
                <li key={v.id}>
                  <VacancyRow v={v} />
                </li>
              ))}
            </ul>
          )
        }
      </QueryBoundary>
    </div>
  )
}

function VacancyRow({ v }: { v: VacancyItem }) {
  return (
    <Link to={`/candidate/vacancies/${v.id}`} className="block rounded-panel-lg border border-hairline p-4 text-ink no-underline hover:bg-panel">
      <div className="flex items-start justify-between gap-3">
        <p className="font-mono text-[20px] font-medium leading-6 tracking-tight text-brand">{salaryLine(v.salary.salary_from, v.salary.salary_to)}</p>
        {v.score != null && <span className="shrink-0 font-mono text-[15px] text-ink-2">Score {Math.round(v.score)}</span>}
      </div>
      <p className="mt-0.5 t-small text-ink-3">{v.salary.salary_type_label} в месяц</p>
      <p className="mt-3 t-label text-ink">{v.title}</p>
      <p className="mt-0.5 t-small text-ink-2">
        {v.company?.name ?? 'Компания'}
        {v.company ? ` · ${v.company.reliability}` : ''}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {v.category && <Chip tone="tint">{v.category}</Chip>}
        {v.format_label && <Chip tone="panel">{v.format_label}</Chip>}
        {v.city && <Chip tone="panel">{cityLabel(v.city)}</Chip>}
        {v.weak_match && <Chip tone="panel">слабое соответствие</Chip>}
        {v.application_status && <Chip tone="brand">вы откликнулись</Chip>}
      </div>
    </Link>
  )
}

// --- Мои отклики -------------------------------------------------------------------------------------------------------

function MyApplications() {
  const q = useQuery({ queryKey: qk.applications, queryFn: () => unwrap(api.GET('/api/applications/mine')) })
  return (
    <QueryBoundary query={q}>
      {(list) =>
        list.length === 0 ? (
          <EmptyState size="compact" title="Откликов пока нет" description="Откройте вакансию и откликнитесь — компания ответит за 7 дней." />
        ) : (
          <ul className="flex flex-col gap-3">
            {list.map((a) => (
              <li key={a.id}>
                <ApplicationRow a={a} />
              </li>
            ))}
          </ul>
        )
      }
    </QueryBoundary>
  )
}

function ApplicationRow({ a }: { a: Application }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [confirm, setConfirm] = useState<'withdraw' | 'revoke-consent' | null>(null)
  const act = useMutation({
    mutationFn: (kind: 'withdraw' | 'revoke-consent') =>
      kind === 'withdraw'
        ? unwrap(api.POST('/api/applications/{application_id}/withdraw', { params: { path: { application_id: a.id } } }))
        : unwrap(api.POST('/api/applications/{application_id}/revoke-consent', { params: { path: { application_id: a.id } } })),
    onSuccess: (_d, kind) => {
      void qc.invalidateQueries({ queryKey: qk.applications })
      void qc.invalidateQueries({ queryKey: ['candidate', 'vacancies'] })
      void qc.invalidateQueries({ queryKey: qk.consents })
      toast.success(kind === 'withdraw' ? 'Отклик отозван, контакты у компании скрыты.' : 'Согласие отозвано, контакты у компании скрыты.')
      setConfirm(null)
    },
  })

  return (
    <div className="flex flex-col gap-2 rounded-panel-lg border border-hairline p-4">
      <p className="t-label text-ink">{a.vacancy?.title ?? 'Вакансия'}</p>
      <p className="t-small text-ink-2">
        {a.company?.name ?? 'Компания'}
        {a.vacancy ? ` · ${salaryLine(a.vacancy.salary.salary_from, a.vacancy.salary.salary_to)}` : ''}
      </p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Chip tone={a.status === 'interested' ? 'tint' : 'panel'}>{a.status_label}</Chip>
        {a.timers.sla_due_text && a.status === 'submitted' && <span className="t-small text-ink-2">ответ компании {a.timers.sla_due_text}</span>}
        {a.submitted_text && <span className="t-small text-ink-3">{a.submitted_text}</span>}
      </div>
      {a.contacts_shared && <p className="t-small text-ink-3">Контакты переданы компании.</p>}
      <div className="flex flex-wrap gap-3">
        {a.actions.includes('withdraw') && (
          <Button size="sm" variant="outline" onClick={() => setConfirm('withdraw')}>
            Отозвать отклик
          </Button>
        )}
        {a.actions.includes('revoke-consent') && (
          <Button size="sm" variant="text" onClick={() => setConfirm('revoke-consent')}>
            Скрыть мои контакты
          </Button>
        )}
      </div>
      <Sheet
        open={confirm != null}
        onClose={() => setConfirm(null)}
        title={confirm === 'withdraw' ? 'Отозвать отклик?' : 'Отозвать согласие на передачу контактов?'}
        description={
          confirm === 'withdraw'
            ? 'Отклик будет отозван, контакты у компании снова скроются.'
            : 'Контакты у компании снова скроются. Отклик останется в истории.'
        }
      >
        <div className="flex flex-col gap-3">
          {act.error ? <Alert tone="danger">{errorMessage(act.error)}</Alert> : null}
          <Button block variant="outline" loading={act.isPending} onClick={() => confirm && act.mutate(confirm)}>
            {confirm === 'withdraw' ? 'Отозвать отклик' : 'Отозвать согласие'}
          </Button>
          <Button block variant="text" onClick={() => setConfirm(null)}>
            Оставить
          </Button>
        </div>
      </Sheet>
    </div>
  )
}
