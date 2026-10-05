import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { errorMessage } from '@/api/errors'
import { useBank, useNeed, usePin, type BankParams } from '@/features/employer/api'
import { InviteDrawer, type InviteCandidate } from '@/features/employer/components/InviteDrawer'
import { BankTable } from '@/features/employer/components/bank/BankTable'
import { BlockTitle, LoadError, PageHead, PageSkeleton } from '@/features/employer/components/common'
import { ProfileGate } from '@/features/employer/components/ProfileGate'
import { CandidatePanel } from '@/features/employer/components/selection/CandidatePanel'
import { GRADE_LABEL, GRADE_ORDER, INDUSTRY_LABEL, MAP_SPECS, SEARCH_MODE_LABEL, SPEC_LABEL, STACK_OPTIONS } from '@/features/employer/dict'
import { cn } from '@/lib/cn'
import { formatInt } from '@/lib/format'
import { Button, Checkbox, EmptyState, Panel, Segmented, useToast } from '@/ui'
import { Select } from '@/ui/Select'

const num = (v: string | null) => {
  const n = Number(v)
  return Number.isInteger(n) && n > 0 ? n : null
}

/** Р9 «Банк кандидатов»: поиск без брифа — специализация, грейд, стек, ФСП, отрасль, режим поиска; 📌 в шортлист брифа. */
export default function BankList() {
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const [showStack, setShowStack] = useState(false)
  const [invite, setInvite] = useState<string | null>(null)
  const [pinBusy, setPinBusy] = useState<string | null>(null)

  // ссылка с карты талантов приходит как ?category=spec:grade — разворачиваем в два фильтра
  useEffect(() => {
    const c = sp.get('category')
    if (!c) return
    const [spec, grade] = c.split(':')
    setSp(
      (cur) => {
        const next = new URLSearchParams(cur)
        next.delete('category')
        if (spec) next.set('spec', spec)
        if (grade) next.set('grade', grade)
        return next
      },
      { replace: true },
    )
  }, [sp, setSp])

  const spec = sp.get('spec')
  const grade = sp.get('grade')
  const skills = (sp.get('skill') ?? '').split(',').filter(Boolean)
  const all = sp.get('all') === '1'
  const fsp = sp.get('fsp') === '1'
  const industry = sp.get('industry')
  const mode = sp.get('mode')
  const page = num(sp.get('page')) ?? 1
  const hp = num(sp.get('hp')) ?? 1
  const needId = sp.get('need')
  const refId = sp.get('ref')
  const showHidden = sp.get('hidden') === '1'

  const setParams = (upd: Record<string, string | null>) =>
    setSp(
      (cur) => {
        const next = new URLSearchParams(cur)
        for (const [k, v] of Object.entries(upd)) {
          if (v == null || v === '') next.delete(k)
          else next.set(k, v)
        }
        if (!('page' in upd)) next.delete('page')
        if (!('hp' in upd)) next.delete('hp')
        return next
      },
      { replace: true },
    )

  const params: BankParams = {
    spec,
    grade,
    skills,
    confirmedOnly: !all,
    hasFsp: fsp ? true : null,
    industry,
    searchMode: mode,
    page,
    hiddenPage: hp,
    needId,
  }
  const bank = useBank(params)
  const need = useNeed(needId ?? undefined)
  const pin = usePin(needId ?? '')
  const view = bank.data

  const rowItem = useMemo(
    () => [...(view?.items ?? []), ...(view?.hidden.items ?? [])].find((i) => i.ref === refId) ?? null,
    [view, refId],
  )

  const activeFilters = [spec, grade, skills.length > 0, all, fsp, industry, mode].filter(Boolean).length

  async function togglePin(ref: string, pinned: boolean) {
    if (!needId) return
    setPinBusy(ref)
    try {
      await pin.mutateAsync({ ref, pinned })
      toast.success(pinned ? 'Убрали из шортлиста' : 'Добавили в шортлист потребности')
    } catch (e) {
      toast.error(errorMessage(e), 'Шортлист не изменился')
    } finally {
      setPinBusy(null)
    }
  }

  const inviteCandidates: InviteCandidate[] = invite
    ? [{ ref: invite, pseudonym: rowItem?.pseudonym ?? 'Кандидат', whyYou: [] }]
    : []
  const hiddenPages = view ? Math.max(1, Math.ceil(view.hidden.count / view.page_size)) : 1

  return (
    <div className="flex flex-col gap-8">
      <PageHead
        title="Банк кандидатов"
        lead="Поиск без текста потребности: специализация, грейд и стек. Сортировка — по тесту, достижениям ФСП и свежести."
        actions={
          <Button variant="outline" to="/employer/bank">
            Карта талантов
          </Button>
        }
      />
      <ProfileGate />

      <section aria-label="Фильтры банка" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
          <Select label="Направление" value={spec ?? ''} onChange={(e) => setParams({ spec: e.target.value || null })} wrapperClassName="min-w-[230px]">
            <option value="">Все направления</option>
            {MAP_SPECS.map((s) => (
              <option key={s} value={s}>
                {SPEC_LABEL[s]}
              </option>
            ))}
          </Select>
          <div className="flex flex-col gap-2">
            <span className="t-label text-ink-2">Грейд</span>
            <Segmented
              aria-label="Грейд"
              options={[{ value: '', label: 'Все' }, ...GRADE_ORDER.map((g) => ({ value: g, label: GRADE_LABEL[g] }))]}
              value={grade ?? ''}
              onChange={(v) => setParams({ grade: v || null })}
            />
          </div>
          <Select label="Отрасль" value={industry ?? ''} onChange={(e) => setParams({ industry: e.target.value || null })} wrapperClassName="min-w-[200px]">
            <option value="">Любая</option>
            {Object.entries(INDUSTRY_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
          <Select label="Режим поиска" value={mode ?? ''} onChange={(e) => setParams({ mode: e.target.value || null })} wrapperClassName="min-w-[220px]">
            <option value="">Любой</option>
            {Object.entries(SEARCH_MODE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <Checkbox checked={fsp} onChange={(e) => setParams({ fsp: e.target.checked ? '1' : null })} label="С ФСП" wrapperClassName="shrink-0" />
          <Checkbox
            checked={all}
            onChange={(e) => setParams({ all: e.target.checked ? '1' : null })}
            label="Стек: и заявленные навыки"
            wrapperClassName="shrink-0"
          />
          <button
            type="button"
            aria-expanded={showStack}
            onClick={() => setShowStack((v) => !v)}
            className="t-label text-brand underline-offset-4 hover:underline"
          >
            {showStack ? 'Скрыть стек' : `Стек${skills.length ? ` (${skills.length})` : ''}`}
          </button>
          {activeFilters > 0 && (
            <button
              type="button"
              onClick={() => setSp(new URLSearchParams(needId ? { need: needId } : {}), { replace: true })}
              className="t-label text-ink-3 underline-offset-4 hover:text-ink hover:underline"
            >
              Сбросить фильтры
            </button>
          )}
        </div>
        {showStack && (
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label="Навыки стека">
            {STACK_OPTIONS.map((s) => {
              const on = skills.includes(s.id)
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => setParams({ skill: (on ? skills.filter((x) => x !== s.id) : [...skills, s.id]).join(',') || null })}
                    className={cn(
                      'rounded-pill border px-3 py-1 t-small transition-colors',
                      on ? 'border-brand bg-brand text-white' : 'border-steel bg-white text-ink hover:border-brand hover:text-brand',
                    )}
                  >
                    {s.name}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {bank.isPending && !view ? (
        <PageSkeleton rows={5} />
      ) : bank.isError && !view ? (
        <LoadError error={bank.error} onRetry={() => void bank.refetch()} title="Не удалось загрузить банк" />
      ) : view ? (
        <div className="grid items-start gap-10 xl:grid-cols-[minmax(0,1fr)_480px] xl:gap-12">
          <div className="flex min-w-0 flex-col gap-6">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <p className="t-body text-ink-2">
                Найдено: <span className="t-num-m text-ink">{formatInt(view.total)}</span>
                {view.hidden.count > 0 && <> · скрыто фильтром: {formatInt(view.hidden.count)}</>}
              </p>
              {view.pin_targets.length > 0 ? (
                <Select
                  aria-label="Шортлист какой потребности"
                  value={needId ?? ''}
                  onChange={(e) => setParams({ need: e.target.value || null })}
                  wrapperClassName="min-w-[280px]"
                >
                  <option value="">Закреплять в шортлист…</option>
                  {view.pin_targets.map((t) => (
                    <option key={t.need_id} value={t.need_id}>
                      📌 в шортлист: {t.title || t.category}
                    </option>
                  ))}
                </Select>
              ) : (
                <p className="t-small text-ink-3">
                  Чтобы закреплять кандидатов, <Link to="/employer/needs/new" className="text-brand underline-offset-4 hover:underline">опишите потребность</Link>.
                </p>
              )}
            </div>

            {view.items.length === 0 ? (
              <EmptyState
                size="compact"
                title="По этим фильтрам никого нет"
                description={
                  view.hidden.count > 0
                    ? `Фильтр «${view.hidden.label ?? 'С ФСП'}» никого не удаляет: ${formatInt(view.hidden.count)} кандидатов ждут в группе «Скрыто фильтрами».`
                    : 'Смягчите фильтр: уберите стек или грейд, разрешите заявленные навыки.'
                }
                action={
                  <Button variant="outline" size="sm" onClick={() => setSp(new URLSearchParams(needId ? { need: needId } : {}), { replace: true })}>
                    Сбросить фильтры
                  </Button>
                }
              />
            ) : (
              <div className={bank.isFetching ? 'opacity-80 transition-opacity' : undefined}>
                <BankTable items={view.items} selectedRef={refId} onSelect={(r) => setParams({ ref: r, page: String(page) })} caption="Кандидаты банка" />
                {view.pages > 1 && (
                  <nav aria-label="Страницы" className="mt-3 flex flex-wrap items-center gap-4">
                    <Button variant="text" disabled={page <= 1} onClick={() => setParams({ page: page > 1 ? String(page - 1) : null })}>
                      ← Назад
                    </Button>
                    <span className="t-small text-ink-3">
                      Страница {view.page} из {view.pages}
                    </span>
                    <Button variant="text" disabled={page >= view.pages} onClick={() => setParams({ page: String(page + 1) })}>
                      Вперёд →
                    </Button>
                  </nav>
                )}
              </div>
            )}

            {view.hidden.count > 0 && (
              <section aria-label="Скрыто фильтрами" className="flex flex-col gap-2">
                <p className="t-body text-ink-2">
                  Скрыто фильтрами ({formatInt(view.hidden.count)}){view.hidden.label ? `: ${view.hidden.label}` : ''}.{' '}
                  <button
                    type="button"
                    aria-expanded={showHidden}
                    onClick={() => setParams({ hidden: showHidden ? null : '1', page: String(page) })}
                    className="t-label text-brand underline-offset-4 hover:underline"
                  >
                    {showHidden ? 'Свернуть' : 'Показать'}
                  </button>
                </p>
                {showHidden && (
                  <>
                    <BlockTitle>Скрытые</BlockTitle>
                    <BankTable hidden items={view.hidden.items} selectedRef={refId} onSelect={(r) => setParams({ ref: r, page: String(page) })} caption="Кандидаты, скрытые фильтром" />
                    {hiddenPages > 1 && (
                      <div className="flex gap-4">
                        <Button variant="text" disabled={hp <= 1} onClick={() => setParams({ hp: hp > 1 ? String(hp - 1) : null, page: String(page), hidden: '1' })}>
                          ← Назад
                        </Button>
                        <Button variant="text" disabled={hp >= hiddenPages} onClick={() => setParams({ hp: String(hp + 1), page: String(page), hidden: '1' })}>
                          Вперёд →
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </section>
            )}
          </div>

          <div className="xl:sticky xl:top-6">
            <Panel variant="panel" radius="md" padding="lg" aria-label="Карточка кандидата">
              {refId ? (
                <CandidatePanel
                  key={refId}
                  needId={null}
                  refId={refId}
                  rowItem={null}
                  bankItem={rowItem}
                  weights={view.weights}
                  pinned={!!rowItem?.shortlisted}
                  pinBusy={pinBusy === refId}
                  invited={rowItem?.invitation_label ?? null}
                  onTogglePin={() => {
                    if (!needId) {
                      toast.show({ message: 'Выберите справа вверху потребность, в шортлист которой закрепить кандидата.' })
                      return
                    }
                    void togglePin(refId, !!rowItem?.shortlisted)
                  }}
                  onInvite={() => setInvite(refId)}
                  onClose={() => setParams({ ref: null, page: String(page) })}
                />
              ) : (
                <EmptyState
                  size="compact"
                  image="detector"
                  title="Выберите кандидата"
                  description="Нажмите на строку: справа откроется слепая карточка. Имя и контакты — только после «да»."
                />
              )}
            </Panel>
          </div>
        </div>
      ) : null}

      <InviteDrawer open={invite != null} onClose={() => setInvite(null)} need={need.data ?? null} candidates={inviteCandidates} />
    </div>
  )
}
