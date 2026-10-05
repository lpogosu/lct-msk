import { useMemo, useState } from 'react'
import { errorMessage } from '@/api/errors'
import {
  useApplications,
  useNeeds,
  useVacancies,
  useVacancyMutations,
  type ApplicationCard,
  type VacancyEmployer,
} from '@/features/employer/api'
import { BlockTitle, LoadError, PageHead, PageSkeleton, Tag } from '@/features/employer/components/common'
import { ProfileGate } from '@/features/employer/components/ProfileGate'
import { ApplicationDrawer } from '@/features/employer/components/vacancies/ApplicationDrawer'
import { VacancyDrawer } from '@/features/employer/components/vacancies/VacancyDrawer'
import { cityLabel } from '@/features/employer/dict'
import { formatDecimal } from '@/lib/format'
import { Button, EmptyState, Identicon, useToast } from '@/ui'
import { Select } from '@/ui/Select'

const STATUS_TONE: Record<string, 'brand' | 'danger' | 'default'> = {
  submitted: 'brand',
  viewed: 'default',
  interested: 'brand',
  rejected: 'default',
  no_response: 'danger',
  withdrawn: 'default',
}

/** Р10 «Вакансии и отклики»: мои вакансии из брифов и отклики по Score с таймером 7 дней на ответ. */
export default function Vacancies() {
  const toast = useToast()
  const vacancies = useVacancies()
  const needs = useNeeds()
  const [vacancyFilter, setVacancyFilter] = useState('')
  const apps = useApplications(vacancyFilter || null)
  const { close } = useVacancyMutations()
  const [editor, setEditor] = useState<{ vacancy: VacancyEmployer | null } | null>(null)
  const [openApp, setOpenApp] = useState<ApplicationCard | null>(null)
  const [closingId, setClosingId] = useState<string | null>(null)

  const list = useMemo(() => vacancies.data ?? [], [vacancies.data])
  const publishedNeeds = new Set(list.filter((v) => v.status === 'published').map((v) => v.need_id))
  const freeNeeds = (needs.data ?? []).filter((n) => n.status !== 'closed' && n.status !== 'draft' && !publishedNeeds.has(n.id))
  const byId = useMemo(() => new Map(list.map((v) => [v.id, v])), [list])

  const skillsOf = (vacancyId: string) => {
    const covered = ((byId.get(vacancyId)?.how_we_check as { covered?: { skill_id: string; name: string; kind: string }[] } | null | undefined)?.covered ?? []).filter(
      (c) => c.kind === 'must',
    )
    return covered.map((c) => ({ id: c.skill_id, name: c.name }))
  }

  async function closeVacancy(v: VacancyEmployer) {
    setClosingId(v.id)
    try {
      await close.mutateAsync(v.id)
      toast.success('Вакансия снята с публикации. Поданные отклики остаются — ответьте на них в срок.')
    } catch (e) {
      toast.error(errorMessage(e), 'Не получилось снять вакансию')
    } finally {
      setClosingId(null)
    }
  }

  return (
    <div className="flex flex-col gap-10">
      <PageHead
        title="Вакансии и отклики"
        lead="Опубликуйте бриф как вакансию — кандидаты по вашим категориям откликнутся сами. Отклики ранжируются тем же Score, что и подборка, контакты видны сразу."
        actions={
          <Button onClick={() => setEditor({ vacancy: null })} disabled={freeNeeds.length === 0}>
            Опубликовать бриф
          </Button>
        }
      />
      <ProfileGate />
      {freeNeeds.length === 0 && !vacancies.isPending && (needs.data?.length ?? 0) === 0 && (
        <p className="t-small text-ink-3">Сначала опишите потребность — её можно будет опубликовать как вакансию.</p>
      )}

      <section aria-labelledby="vac-title" className="flex flex-col gap-4">
        <BlockTitle>
          <span id="vac-title">Мои вакансии</span>
        </BlockTitle>
        {vacancies.isPending ? (
          <PageSkeleton rows={2} />
        ) : vacancies.isError ? (
          <LoadError error={vacancies.error} onRetry={() => void vacancies.refetch()} />
        ) : list.length === 0 ? (
          <EmptyState
            size="compact"
            title="Вакансий пока нет"
            description="Опубликуйте бриф как вакансию: тот же текст и вилка, отклики придут с контактами."
            action={
              freeNeeds.length > 0 ? (
                <Button onClick={() => setEditor({ vacancy: null })}>Опубликовать бриф</Button>
              ) : (
                <Button to="/employer/needs/new">Описать потребность</Button>
              )
            }
          />
        ) : (
          <ul className="m-0 flex list-none flex-col border-t border-hairline p-0">
            {list.map((v) => (
              <li key={v.id} className="grid gap-x-8 gap-y-3 border-b border-hairline py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="t-name text-ink [overflow-wrap:anywhere]">{v.title}</p>
                    <Tag tone={v.status === 'published' ? 'brand' : 'default'}>{v.status === 'published' ? 'опубликована' : 'снята'}</Tag>
                    {v.applications_new > 0 && <Tag tone="brand">новых откликов: {v.applications_new}</Tag>}
                  </div>
                  <p className="mt-1 t-body text-ink-2">
                    {v.salary.text}
                    {v.format_label ? ` · ${v.format_label}` : ''}
                    {v.city ? ` · ${cityLabel(v.city)}` : ''}
                  </p>
                  <p className="t-small text-ink-3">
                    {v.published_text ? `Опубликована ${v.published_text}` : ''} · откликов: {v.applications_total}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                  {v.applications_total > 0 && (
                    <Button variant="outline" size="sm" onClick={() => setVacancyFilter(v.id)}>
                      Показать отклики
                    </Button>
                  )}
                  {v.status === 'published' && (
                    <>
                      <Button variant="text" onClick={() => setEditor({ vacancy: v })}>
                        Редактировать
                      </Button>
                      <Button variant="text" loading={closingId === v.id} onClick={() => void closeVacancy(v)}>
                        Снять с публикации
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="apps-title" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <BlockTitle>
            <span id="apps-title">Отклики</span>
          </BlockTitle>
          {list.length > 1 && (
            <Select aria-label="Вакансия" value={vacancyFilter} onChange={(e) => setVacancyFilter(e.target.value)} wrapperClassName="min-w-[280px]">
              <option value="">Все вакансии</option>
              {list.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.title}
                </option>
              ))}
            </Select>
          )}
        </div>
        {apps.isPending ? (
          <PageSkeleton rows={2} />
        ) : apps.isError ? (
          <LoadError error={apps.error} onRetry={() => void apps.refetch()} />
        ) : (apps.data ?? []).length === 0 ? (
          <EmptyState
            size="compact"
            title="Откликов пока нет"
            description="Когда кандидат откликнется, он появится здесь с Score и контактами. На ответ — 7 дней, иначе минус к надёжности компании."
          />
        ) : (
          <ul className="m-0 flex list-none flex-col border-t border-hairline p-0">
            {apps.data?.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => setOpenApp(a)}
                  className="grid w-full grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-b border-hairline px-1 py-4 text-left transition-colors hover:bg-panel"
                >
                  <Identicon seed={a.pseudonym} size={28} />
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2 t-name text-ink">
                      {a.pseudonym}
                      <Tag tone={STATUS_TONE[a.status] ?? 'default'}>{a.status_label}</Tag>
                    </p>
                    <p className="t-small text-ink-2">
                      {a.category} · {byId.get(a.vacancy_id)?.title ?? 'вакансия'} · {a.submitted_text}
                    </p>
                    {a.reject_reason_label && <p className="t-small text-ink-3">Отказ: {a.reject_reason_label}</p>}
                    {a.timers.sla_due_text && <p className="t-small text-danger-text">Ответить {a.timers.sla_due_text}</p>}
                  </div>
                  <span className="t-num-l text-ink">{a.score != null ? formatDecimal(a.score, 0) : '—'}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {editor && (
        <VacancyDrawer
          key={editor.vacancy?.id ?? 'new'}
          vacancy={editor.vacancy}
          needs={freeNeeds}
          onClose={() => setEditor(null)}
        />
      )}
      {openApp && <ApplicationDrawer key={openApp.id} id={openApp.id} skills={skillsOf(openApp.vacancy_id)} onClose={() => setOpenApp(null)} />}
    </div>
  )
}
