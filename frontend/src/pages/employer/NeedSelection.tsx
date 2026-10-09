import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import {
  useCreateVersion,
  useInvalidateNeed,
  useNeed,
  useNeedCategories,
  usePin,
  useRollback,
  useSelection,
  useVersions,
  type FilterIn,
  type Need,
  type SelectionItem,
  type VersionView,
} from '@/features/employer/api'
import { InviteDrawer, type InviteCandidate } from '@/features/employer/components/InviteDrawer'
import { BlockTitle, LoadError, PageHead, PageSkeleton } from '@/features/employer/components/common'
import { ProfileGate } from '@/features/employer/components/ProfileGate'
import { EvidenceSlot } from '@/features/employer/components/selection/EvidenceSlot'
import { CandidatePanel } from '@/features/employer/components/selection/CandidatePanel'
import { FilterBar } from '@/features/employer/components/selection/FilterBar'
import { SelectionTable } from '@/features/employer/components/selection/SelectionTable'
import { CheckSetDrawer } from '@/features/employer/components/selection/CheckSetDrawer'
import { Shortlist } from '@/features/employer/components/selection/Shortlist'
import { CompareBar } from '@/features/employer/components/selection/CompareBar'
import { CompareDialog } from '@/features/employer/components/selection/CompareDialog'
import { useCompareSet } from '@/features/employer/compare'
import { VersionBar } from '@/features/employer/components/selection/VersionBar'
import { FORMAT_LABEL_LOWER, type ToggleFilterCode } from '@/features/employer/dict'
import { catKey } from '@/features/employer/reach'
import { formatDayMonth, formatInt, formatRubRangeK } from '@/lib/format'
import { Alert, Button, Counters, EmptyState, Panel, Segmented, useToast } from '@/ui'
import { Select } from '@/ui/Select'
import { Tabs } from '@/ui/Tabs'

const SHORTLIST_LIMIT = 30 // config/scoring.yaml: selection.shortlist_max (точное значение приходит в GET …/shortlist)
const BATCH_MAX = 10 // config/contact.yaml: quotas.batch_max
const PAGE_SIZE = 10

const num = (v: string | null) => {
  const n = Number(v)
  return Number.isInteger(n) && n > 0 ? n : null
}

/** Р5+Р6 «Подборка»: версии, фильтры, таблица со слепыми строками и панель кандидата; шортлист и приглашение. */
export default function NeedSelection() {
  const { id = '' } = useParams()
  const need = useNeed(id)
  if (need.isPending) return <PageSkeleton rows={5} />
  if (need.isError) return <LoadError error={need.error} onRetry={() => void need.refetch()} title="Не удалось открыть потребность" />
  return <SelectionScreen key={need.data.id} need={need.data} />
}

function summarize(need: Need): string {
  const b = need.brief
  const parts: string[] = [`${formatRubRangeK(b.salary_from, b.salary_to)} ${b.salary_net ? 'на руки' : 'до вычета НДФЛ'}`]
  const where = [
    b.formats.map((f) => FORMAT_LABEL_LOWER[f] ?? f).join(' или '),
    need.parse.city?.name ? `в городе ${need.parse.city.name}` : '',
  ]
    .filter(Boolean)
    .join(' ')
  if (where) parts.push(where)
  const must = need.parse.skills.filter((s) => s.kind === 'must').map((s) => s.name)
  const nice = need.parse.skills.filter((s) => s.kind === 'nice').map((s) => s.name)
  let tail = ''
  if (must.length) tail += `Нужны ${must.join(', ')}`
  if (nice.length) tail += `${tail ? '; ' : ''}${nice.join(', ')} будут плюсом`
  return `${parts.join(', ')}.${tail ? ` ${tail}.` : ''}`
}

function SelectionScreen({ need }: { need: Need }) {
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const version = num(sp.get('v'))
  const tab = sp.get('tab') === 'category' ? 'category' : 'overall'
  const cat = sp.get('cat')
  const page = num(sp.get('page')) ?? 1
  const hp = num(sp.get('hp')) ?? 1
  const refId = sp.get('ref')
  const showHidden = sp.get('hidden') === '1'

  const setParams = (upd: Record<string, string | null>, opts?: { replace?: boolean }) =>
    setSp(
      (cur) => {
        const next = new URLSearchParams(cur)
        for (const [k, v] of Object.entries(upd)) {
          if (v == null || v === '') next.delete(k)
          else next.set(k, v)
        }
        return next
      },
      { replace: opts?.replace ?? true },
    )

  const sel = useSelection(need.id, { version, tab, category: tab === 'category' ? cat : null, page, hiddenPage: hp })
  const versions = useVersions(need.id)
  const cats = useNeedCategories(need.id)
  const pin = usePin(need.id)
  const createVersion = useCreateVersion(need.id)
  const rollback = useRollback(need.id)
  const [inviteRefs, setInviteRefs] = useState<string[] | null>(null)
  const [checkSetOpen, setCheckSetOpen] = useState(false)
  const [pinBusy, setPinBusy] = useState<string | null>(null)
  // Сравнение до 3 кандидатов бок о бок (только слепая карточка); выбор живёт в localStorage и переживает фильтры
  const compare = useCompareSet(need.id)
  const [compareOpen, setCompareOpen] = useState(false)
  function toggleCompare(it: SelectionItem) {
    if (compare.toggle({ ref: it.ref, pseudonym: it.pseudonym }) === 'full') toast.error('Сравнить можно не больше трёх кандидатов')
  }

  const view: VersionView | undefined = sel.data
  const notBuilt = sel.error instanceof ApiException && sel.error.code === 'selection_not_built'
  const currentN = versions.data?.current ?? need.current_version
  const closed = need.status === 'closed'

  // известные строки: страница, скрытые, шортлист
  const known = useMemo(() => {
    const m = new Map<string, SelectionItem>()
    for (const it of view?.items ?? []) m.set(it.ref, it)
    for (const it of view?.hidden.items ?? []) m.set(it.ref, it)
    for (const e of view?.shortlist ?? []) if (e.item) m.set(e.ref, e.item)
    return m
  }, [view])

  const shortlist = view?.shortlist ?? []
  const invitable = shortlist.filter((e) => !e.item?.invitation_status)
  const batchRefs = invitable.slice(0, BATCH_MAX).map((e) => e.ref)

  function toCandidates(refs: string[]): InviteCandidate[] {
    return refs.map((r) => {
      const it = known.get(r)
      const entry = shortlist.find((e) => e.ref === r)
      return {
        ref: r,
        pseudonym: it?.pseudonym ?? entry?.pseudonym ?? 'Кандидат',
        whyYou: it?.why_you ?? [],
        salarySignal: it?.salary?.signal ?? null,
      }
    })
  }

  const currentFilters: FilterIn[] = (view?.filters ?? []).map((f) => ({ code: f.code as FilterIn['code'], value: f.value ?? null }))

  async function applyVersion(body: { filters?: FilterIn[]; categories?: string[] }) {
    try {
      await createVersion.mutateAsync({
        filters: body.filters,
        categories: body.categories?.map((k) => {
          const [spec, grade] = k.split(':')
          return { spec, grade: grade as 'intern' | 'junior' | 'middle' | 'senior' }
        }),
      })
      setParams({ v: null, page: null, hp: null, tab: null, cat: null })
    } catch (e) {
      toast.error(`${errorMessage(e)} Осталась прошлая версия.`, 'Не удалось пересчитать подборку')
    }
  }

  const onToggleFilter = (code: ToggleFilterCode, on: boolean) => {
    const next = on ? [...currentFilters.filter((f) => f.code !== code), { code } as FilterIn] : currentFilters.filter((f) => f.code !== code)
    void applyVersion({ filters: next })
  }

  async function onRollback(n: number) {
    try {
      await rollback.mutateAsync(n)
      setParams({ v: null, page: null, hp: null, tab: null, cat: null })
      toast.success(`Вернулись к версии v${n}. Бриф тоже вернулся к её снимку.`)
    } catch (e) {
      toast.error(errorMessage(e), 'Не удалось откатиться')
    }
  }

  async function togglePin(ref: string, pinned: boolean) {
    setPinBusy(ref)
    try {
      await pin.mutateAsync({ ref, pinned })
      toast.success(pinned ? 'Убрали из шортлиста' : 'Добавили в шортлист')
    } catch (e) {
      toast.error(errorMessage(e), 'Шортлист не изменился')
    } finally {
      setPinBusy(null)
    }
  }

  // счётчики по выбранным категориям
  const counters = useMemo(() => {
    if (!view) return []
    const chosen = (cats.data?.categories ?? []).filter((c) => view.categories.some((v) => catKey(v) === catKey(c)))
    const items = [
      {
        value: formatInt(view.visible_n),
        label: view.categories.length === 1 ? `в категории ${view.categories[0].name}` : 'в выбранных категориях',
      },
    ]
    if (chosen.length > 0 && chosen.every((c) => c.reach.shown && c.reach.reach_at_to != null)) {
      items.push({ value: formatInt(chosen.reduce((s, c) => s + (c.reach.reach_at_to ?? 0), 0)), label: 'подходят по вашей вилке' })
    }
    if (chosen.length > 0 && chosen.every((c) => c.share_fsp != null && c.n != null)) {
      items.push({
        value: formatInt(chosen.reduce((s, c) => s + Math.round((c.share_fsp ?? 0) * (c.n ?? 0)), 0)),
        label: 'с достижениями ФСП',
      })
    }
    return items
  }, [view, cats.data])

  const newCount = view && view.parent_n != null ? view.items.filter((i) => i.badge?.kind === 'new').length : null
  const hiddenPages = view ? Math.max(1, Math.ceil(view.hidden.count / PAGE_SIZE)) : 1
  const categoryOptions = (view?.categories ?? []).map((c) => ({ value: catKey(c), label: c.name }))

  const selectedItem = refId ? (known.get(refId) ?? null) : null
  const selectedPinned = refId ? shortlist.some((e) => e.ref === refId) : false

  return (
    <div className="flex flex-col gap-8">
      <PageHead
        eyebrow={`Потребность от ${formatDayMonth(need.created_at)}`}
        title={need.title || need.category}
        lead={summarize(need)}
        actions={
          <>
            <Link to={`/employer/needs/${need.id}/edit`} className="t-label text-brand underline-offset-4 hover:underline">
              Изменить бриф
            </Link>
            {!closed && (
              <div className="flex flex-col items-start gap-1">
                <Button
                  disabled={batchRefs.length === 0}
                  onClick={() => setInviteRefs(batchRefs)}
                  title={batchRefs.length === 0 ? 'Закрепите кандидатов в шортлист' : undefined}
                >
                  {batchRefs.length > 0 ? `Пригласить из шортлиста (${batchRefs.length})` : 'Пригласить из шортлиста'}
                </Button>
                {batchRefs.length === 0 && (
                  <span className="t-small text-ink-3">
                    {shortlist.length === 0 ? 'Сначала закрепите кандидатов 📌' : 'Все из шортлиста уже приглашены'}
                  </span>
                )}
              </div>
            )}
          </>
        }
      />
      <ProfileGate />
      {closed && (
        <Alert tone="info" title="Потребность закрыта">
          {need.close_reason === 'filled' ? 'Сотрудника нашли.' : 'Поиск отменён.'} Подборка осталась для просмотра, новые приглашения не отправляются.
        </Alert>
      )}

      {sel.isPending && !view ? (
        <PageSkeleton rows={5} />
      ) : notBuilt ? (
        <EmptyState
          title="Подборку по этой потребности ещё не собирали"
          description="Соберём её по вашему брифу: категории, вилка и навыки уже разобраны."
          action={
            <Button loading={createVersion.isPending} onClick={() => void applyVersion({})}>
              Собрать подборку
            </Button>
          }
        />
      ) : sel.isError && !view ? (
        <LoadError error={sel.error} onRetry={() => void sel.refetch()} title="Не удалось открыть подборку" />
      ) : view ? (
        <>
          <div className="flex flex-col gap-5">
            <VersionBar
              view={view}
              versions={versions.data?.items ?? []}
              rollbackTargets={versions.data?.rollback_targets ?? []}
              currentN={currentN ?? null}
              onOpen={(n) => setParams({ v: n ? String(n) : null, page: null, hp: null })}
              onRollback={(n) => void onRollback(n)}
              busy={rollback.isPending}
            />
            <FilterBar
              view={view}
              categories={cats.data?.categories ?? []}
              disabled={createVersion.isPending || (currentN != null && view.n !== currentN) || closed}
              onCategories={(keys) => void applyVersion({ filters: currentFilters, categories: keys })}
              onToggleFilter={onToggleFilter}
            />
            {createVersion.isPending && (
              <p role="status" className="t-small text-ink-3">
                Пересчитываем подборку — соберём новую версию…
              </p>
            )}
            <Counters
              items={[
                ...counters,
                ...(newCount != null
                  ? [{ value: formatInt(newCount), label: view.pages > 1 ? 'новых на странице с прошлой версии' : 'новых с прошлой версии', tone: 'brand' as const }]
                  : []),
              ]}
            />
          </div>

          <div className="grid items-start gap-10 xl:grid-cols-[minmax(0,1fr)_480px] xl:gap-12">
            <div className="flex min-w-0 flex-col gap-8">
              {view.categories.length > 1 && (
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <Tabs
                    aria-label="Вид подборки"
                    value={tab}
                    onChange={(t) => setParams({ tab: t === 'category' ? 'category' : null, cat: null, page: null })}
                    options={[
                      { value: 'overall', label: 'Общий топ' },
                      { value: 'category', label: 'По категориям' },
                    ]}
                  />
                  {tab === 'category' && (
                    <Select
                      aria-label="Категория"
                      value={cat ?? categoryOptions[0]?.value ?? ''}
                      onChange={(e) => setParams({ cat: e.target.value, page: null })}
                      wrapperClassName="min-w-[260px]"
                    >
                      {categoryOptions.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </Select>
                  )}
                </div>
              )}

              <section aria-label="Таблица подборки" className={sel.isFetching ? 'opacity-80 transition-opacity' : undefined}>
                {view.items.length === 0 ? (
                  view.hidden_n > 0 ? (
                    <EmptyState
                      size="compact"
                      title={`Все ${formatInt(view.hidden_n)} скрыты фильтрами`}
                      description="Фильтры никого не удаляют: кандидаты ждут в группе «Скрыто фильтрами». Снимите фильтры, чтобы увидеть их снова."
                      action={
                        <Button
                          variant="outline"
                          size="sm"
                          loading={createVersion.isPending}
                          onClick={() => void applyVersion({ filters: [] })}
                        >
                          Снять все фильтры
                        </Button>
                      }
                    />
                  ) : (
                    <EmptyState
                      size="compact"
                      title="В выбранных категориях пока никого нет"
                      description="Расширьте вилку, добавьте соседний грейд или загляните в банк кандидатов."
                      action={
                        <div className="flex flex-wrap justify-center gap-3">
                          <Button variant="outline" size="sm" to={`/employer/needs/${need.id}/edit`}>
                            Изменить бриф
                          </Button>
                          <Button variant="outline" size="sm" to="/employer/bank">
                            Банк кандидатов
                          </Button>
                        </div>
                      }
                    />
                  )
                ) : (
                  <SelectionTable
                    items={view.items}
                    selectedRef={refId}
                    onSelect={(r) => setParams({ ref: r })}
                    caption="Кандидаты подборки по убыванию Score"
                    compareRefs={compare.refs}
                    onCompareToggle={toggleCompare}
                  />
                )}
                {view.pages > 1 && (
                  <Pager
                    page={view.page}
                    pages={view.pages}
                    label={`Показаны ${(view.page - 1) * view.page_size + 1}–${(view.page - 1) * view.page_size + view.items.length} из ${formatInt(view.items_total)}`}
                    onPage={(p) => setParams({ page: p > 1 ? String(p) : null, ref: null })}
                  />
                )}
                {view.hidden.count > 0 && (
                  <p className="mt-3 t-body text-ink-2">
                    Скрыто фильтрами ({formatInt(view.hidden.count)}):{' '}
                    {view.hidden_summary.map((h) => `${h.label} — ${h.count}`).join('; ')}.{' '}
                    <button
                      type="button"
                      aria-expanded={showHidden}
                      onClick={() => setParams({ hidden: showHidden ? null : '1', hp: null })}
                      className="t-label text-brand underline-offset-4 hover:underline"
                    >
                      {showHidden ? 'Свернуть' : 'Показать'}
                    </button>
                  </p>
                )}
              </section>

              {showHidden && view.hidden.items.length > 0 && (
                <section aria-label="Скрыто фильтрами" className="flex flex-col gap-2">
                  <BlockTitle>Скрыто фильтрами ({formatInt(view.hidden.count)})</BlockTitle>
                  <SelectionTable
                    hidden
                    items={view.hidden.items}
                    selectedRef={refId}
                    onSelect={(r) => setParams({ ref: r })}
                    caption="Кандидаты, скрытые фильтрами, с причиной"
                    compareRefs={compare.refs}
                    onCompareToggle={toggleCompare}
                  />
                  {hiddenPages > 1 && (
                    <Pager page={view.hidden.page} pages={hiddenPages} onPage={(p) => setParams({ hp: p > 1 ? String(p) : null })} />
                  )}
                </section>
              )}

              <Shortlist
                entries={shortlist}
                limit={SHORTLIST_LIMIT}
                selectedRef={refId}
                busyRef={pinBusy}
                onOpen={(r) => setParams({ ref: r })}
                onRemove={(r) => void togglePin(r, true)}
                onCheckSet={closed ? undefined : () => setCheckSetOpen(true)}
              />

              <NeedStatusControls need={need} />
              <CompareBar
                entries={compare.entries}
                onRemove={compare.remove}
                onClear={compare.clear}
                onOpen={() => setCompareOpen(true)}
              />
              <CompareDialog
                open={compareOpen}
                onClose={() => setCompareOpen(false)}
                needId={need.id}
                entries={compare.entries}
                known={known}
                weights={view.weights}
                versionNote={currentN != null && view.n !== currentN ? `Показана версия подборки ${view.n}, текущая — ${currentN}` : null}
                closed={closed}
                onRemove={compare.remove}
                onInvite={(r) => {
                  setCompareOpen(false)
                  setInviteRefs([r])
                }}
                onOpenCard={(r) => {
                  setCompareOpen(false)
                  setParams({ ref: r })
                }}
              />
            </div>

            <div className="xl:sticky xl:top-6">
              <EvidenceSlot
                open={!!refId}
                onClose={() => setParams({ ref: null })}
                suspended={inviteRefs != null}
                empty={
                  <EmptyState
                    size="compact"
                    image="detector"
                    title="Выберите кандидата"
                    description="Нажмите на строку таблицы: откроется слепая карточка с разложением Score и доказательствами. Имя и контакты — только после «да»."
                  />
                }
              >
                {refId && (
                  <CandidatePanel
                    key={refId}
                    needId={need.id}
                    refId={refId}
                    rowItem={selectedItem}
                    weights={view.weights}
                    pinned={selectedPinned}
                    pinBusy={pinBusy === refId}
                    invited={selectedItem?.invitation_label ?? null}
                    onTogglePin={() => void togglePin(refId, selectedPinned)}
                    onInvite={() => setInviteRefs([refId])}
                    onClose={() => setParams({ ref: null })}
                  />
                )}
              </EvidenceSlot>
            </div>
          </div>
        </>
      ) : null}

      {checkSetOpen && <CheckSetDrawer needId={need.id} entries={shortlist} onClose={() => setCheckSetOpen(false)} />}
      <InviteDrawer
        open={inviteRefs != null}
        onClose={() => setInviteRefs(null)}
        need={need}
        candidates={inviteRefs ? toCandidates(inviteRefs) : []}
      />
    </div>
  )
}

function Pager({ page, pages, onPage, label }: { page: number; pages: number; onPage: (p: number) => void; label?: string }) {
  return (
    <nav aria-label="Страницы" className="mt-3 flex flex-wrap items-center gap-4">
      <Button variant="text" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ← Назад
      </Button>
      <span className="t-small text-ink-3">
        {label ?? `Страница ${page} из ${pages}`}
        {label ? ` · страница ${page} из ${pages}` : ''}
      </span>
      <Button variant="text" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Вперёд →
      </Button>
    </nav>
  )
}

/** Пауза, возобновление и закрытие потребности (закрытие отзывает ждущие ответа приглашения). */
function NeedStatusControls({ need }: { need: Need }) {
  const toast = useToast()
  const invalidate = useInvalidateNeed(need.id)
  const [confirm, setConfirm] = useState(false)
  const [reason, setReason] = useState<'filled' | 'cancelled'>('filled')
  const change = useMutation({
    mutationFn: (body: { status: 'active' | 'paused' | 'closed'; close_reason?: 'filled' | 'cancelled' }) =>
      unwrap(api.PATCH('/api/needs/{need_id}', { params: { path: { need_id: need.id } }, body: { ...body, clear_city: false } })),
    onSuccess: (r, vars) => {
      invalidate()
      setConfirm(false)
      if (vars.status === 'closed') {
        toast.success(
          r.withdrawn_invitations ? `Потребность закрыта. Отозвано приглашений: ${r.withdrawn_invitations}.` : 'Потребность закрыта',
        )
      } else toast.success(vars.status === 'paused' ? 'Потребность на паузе' : 'Потребность снова активна')
    },
    onError: (e) => toast.error(errorMessage(e), 'Статус не изменился'),
  })

  if (need.status === 'closed') return null
  return (
    <section aria-label="Управление потребностью" className="flex flex-col gap-3 border-t border-hairline pt-6">
      <BlockTitle>Управление потребностью</BlockTitle>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {need.status === 'paused' ? (
          <Button variant="outline" size="sm" loading={change.isPending} onClick={() => change.mutate({ status: 'active' })}>
            Возобновить поиск
          </Button>
        ) : (
          <Button variant="outline" size="sm" loading={change.isPending} onClick={() => change.mutate({ status: 'paused' })}>
            Поставить на паузу
          </Button>
        )}
        {!confirm && (
          <Button variant="text" onClick={() => setConfirm(true)}>
            Закрыть потребность…
          </Button>
        )}
      </div>
      {confirm && (
        <Panel variant="panel" radius="md" padding="sm" className="flex flex-col gap-3">
          <p className="t-body text-ink">
            Закрытие отзовёт приглашения, на которые ещё не ответили, и кандидаты получат причину. Подборка останется для просмотра.
          </p>
          <Segmented
            aria-label="Причина закрытия"
            options={[
              { value: 'filled', label: 'Нашли сотрудника' },
              { value: 'cancelled', label: 'Поиск отменён' },
            ]}
            value={reason}
            onChange={setReason}
          />
          <div className="flex gap-3">
            <Button size="sm" loading={change.isPending} onClick={() => change.mutate({ status: 'closed', close_reason: reason })}>
              Закрыть потребность
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirm(false)}>
              Не закрывать
            </Button>
          </div>
        </Panel>
      )}
    </section>
  )
}
