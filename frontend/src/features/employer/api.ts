import { keepPreviousData, useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { Schemas } from '@/api/types'

// Запросы кабинета работодателя поверх типизированного клиента. Ключи кэша — в одном месте,
// чтобы после записи (бриф, версия, приглашение) инвалидировать связанное и не рассинхронизировать счётчики.

export type CompanyMe = Schemas['CompanyMeOut']
export type CompanyProfileIn = Schemas['CompanyProfileIn']
export type Need = Schemas['NeedOut']
export type NeedSummary = Schemas['NeedSummaryOut']
export type ParseOut = Schemas['ParseOut']
export type BriefParseIn = Schemas['BriefParseIn']
export type CategoryRec = Schemas['CategoryRecOut']
type ReachRaw = Schemas['ReachOut']
/** openapi-fetch расширяет кортежи [верх, число] до number[][] — описываем форму, которая реально приходит. */
export type CategoryCard = Omit<Schemas['CategoryCardOut'], 'reach'> & {
  reach: Omit<ReachRaw, 'histogram'> & { histogram: { step: number; bins: number[][] } | null }
}
export type CheckOut = Schemas['CheckOut']
export type VersionView = Schemas['VersionViewOut']
export type VersionMeta = Schemas['VersionMetaOut']
export type SelectionItem = Schemas['SelectionItemOut']
export type ShortlistEntry = Schemas['ShortlistEntryOut']
export type CandidateCard = Schemas['CandidateCardOut']
export type FilterIn = Schemas['FilterIn']
export type BankOut = Schemas['app__services__matching__schemas__BankOut']
export type BankItem = Schemas['BankItemOut']
export type Kanban = Schemas['KanbanOut']
export type InvitationCard = Schemas['InvitationEmployerCard']
export type InvitationDetail = Schemas['InvitationEmployerDetail']
export type InvitationForm = Schemas['InvitationBatchIn']
export type InvitationCreate = Schemas['InvitationCreateIn']
export type InvitationPreview = Schemas['InvitationPreviewOut']
export type SendResult = Schemas['SendResultOut']
export type Quota = Schemas['QuotaOut']
export type VacancyEmployer = Schemas['VacancyEmployerOut']
export type ApplicationCard = Schemas['ApplicationEmployerCard']
export type ApplicationDetail = Schemas['ApplicationEmployerDetail']
export type TaskOut = Schemas['TaskOut']

export const qk = {
  company: ['employer', 'company'] as const,
  needs: ['employer', 'needs'] as const,
  need: (id: string) => ['employer', 'need', id] as const,
  categories: (id: string) => ['employer', 'categories', id] as const,
  check: (id: string) => ['employer', 'check', id] as const,
  versions: (id: string) => ['employer', 'versions', id] as const,
  selection: (id: string, ...rest: unknown[]) => ['employer', 'selection', id, ...rest] as const,
  selectionAll: (id: string) => ['employer', 'selection', id] as const,
  shortlist: (id: string) => ['employer', 'shortlist', id] as const,
  card: (id: string, ref: string) => ['employer', 'card', id, ref] as const,
  bankCard: (ref: string) => ['employer', 'bank-card', ref] as const,
  bank: ['employer', 'bank'] as const,
  invitations: ['employer', 'invitations'] as const,
  invitation: (id: string) => ['employer', 'invitation', id] as const,
  vacancies: ['employer', 'vacancies'] as const,
  applications: ['employer', 'applications'] as const,
  tasks: ['employer', 'tasks'] as const,
  catalog: ['employer', 'task-catalog'] as const,
}

// --------------------------------------------------------------------------- компания

export function useCompany() {
  return useQuery({
    queryKey: qk.company,
    queryFn: () => unwrap(api.GET('/api/companies/me')),
  })
}

export function useSaveCompany() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CompanyProfileIn) => unwrap(api.PUT('/api/companies/me', { body })),
    onSuccess: (data) => {
      qc.setQueryData(qk.company, data)
    },
  })
}

// --------------------------------------------------------------------------- брифы

export function useNeeds() {
  return useQuery({
    queryKey: qk.needs,
    queryFn: async () => (await unwrap(api.GET('/api/needs'))).items,
  })
}

export function useNeed(id: string | undefined) {
  return useQuery({
    queryKey: qk.need(id ?? ''),
    enabled: !!id,
    queryFn: () => unwrap(api.GET('/api/needs/{need_id}', { params: { path: { need_id: id as string } } })),
  })
}

export function useNeedCategories(id: string | undefined) {
  return useQuery({
    queryKey: qk.categories(id ?? ''),
    enabled: !!id,
    queryFn: () => unwrap(api.GET('/api/needs/{need_id}/categories', { params: { path: { need_id: id as string } } })),
  })
}

export function useNeedCheck(id: string | undefined) {
  return useQuery({
    queryKey: qk.check(id ?? ''),
    enabled: !!id,
    queryFn: () => unwrap(api.GET('/api/needs/{need_id}/check', { params: { path: { need_id: id as string } } })),
  })
}

export function parseBrief(body: BriefParseIn, signal?: AbortSignal) {
  return unwrap(api.POST('/api/needs/parse', { body, signal }))
}

// --------------------------------------------------------------------------- подборка

export interface SelectionParams {
  /** Номер версии; без него — текущая */
  version?: number | null
  tab?: 'overall' | 'category'
  category?: string | null
  page?: number
  hiddenPage?: number
}

export function useVersions(id: string | undefined) {
  return useQuery({
    queryKey: qk.versions(id ?? ''),
    enabled: !!id,
    queryFn: () => unwrap(api.GET('/api/needs/{need_id}/versions', { params: { path: { need_id: id as string } } })),
  })
}

export function useSelection(id: string | undefined, p: SelectionParams) {
  const { version, tab = 'overall', category = null, page = 1, hiddenPage = 1 } = p
  return useQuery({
    queryKey: qk.selection(id ?? '', version ?? 'current', tab, category, page, hiddenPage),
    enabled: !!id,
    placeholderData: keepPreviousData,
    queryFn: () => {
      const query = { tab, category: category ?? undefined, page, hidden_page: hiddenPage }
      if (version) {
        return unwrap(
          api.GET('/api/needs/{need_id}/versions/{n}', {
            params: { path: { need_id: id as string, n: version }, query },
          }),
        )
      }
      return unwrap(api.GET('/api/needs/{need_id}/selection', { params: { path: { need_id: id as string }, query } }))
    },
  })
}

export function useShortlist(id: string | undefined) {
  return useQuery({
    queryKey: qk.shortlist(id ?? ''),
    enabled: !!id,
    queryFn: () => unwrap(api.GET('/api/needs/{need_id}/shortlist', { params: { path: { need_id: id as string } } })),
  })
}

export function useCandidateCard(needId: string | undefined, refId: string | null) {
  return useQuery({
    queryKey: qk.card(needId ?? '', refId ?? ''),
    enabled: !!needId && !!refId,
    queryFn: () =>
      unwrap(
        api.GET('/api/needs/{need_id}/candidates/{ref}', {
          params: { path: { need_id: needId as string, ref: refId as string } },
        }),
      ),
  })
}

/**
 * Слепые карточки нескольких кандидатов (экран «Сравнить»): тот же ключ кэша, что у панели кандидата, —
 * открытая карточка не запрашивается второй раз. Открытие записывается как просмотр (DECISIONS п. 7).
 */
export function useCandidateCards(needId: string, refs: readonly string[], enabled = true) {
  return useQueries({
    queries: refs.map((ref) => ({
      queryKey: qk.card(needId, ref),
      enabled: enabled && !!needId,
      queryFn: () =>
        unwrap(api.GET('/api/needs/{need_id}/candidates/{ref}', { params: { path: { need_id: needId, ref } } })),
    })),
  })
}

/** Любая запись по брифу меняет версии, подборку, счётчики и шортлист — сбрасываем их разом. */
export function useInvalidateNeed(id: string | undefined) {
  const qc = useQueryClient()
  return () => {
    if (!id) return
    void qc.invalidateQueries({ queryKey: qk.need(id) })
    void qc.invalidateQueries({ queryKey: qk.versions(id) })
    void qc.invalidateQueries({ queryKey: qk.selectionAll(id) })
    void qc.invalidateQueries({ queryKey: qk.categories(id) })
    void qc.invalidateQueries({ queryKey: qk.check(id) })
    void qc.invalidateQueries({ queryKey: qk.shortlist(id) })
    void qc.invalidateQueries({ queryKey: qk.needs })
  }
}

export function useCreateVersion(id: string) {
  const qc = useQueryClient()
  const invalidate = useInvalidateNeed(id)
  return useMutation({
    mutationFn: (body: Schemas['VersionCreateIn']) =>
      unwrap(api.POST('/api/needs/{need_id}/versions', { params: { path: { need_id: id } }, body })),
    onSuccess: (data) => {
      // свежая версия сразу становится текущей: кладём её в кэш, чтобы экран не мигал
      qc.setQueryData(qk.selection(id, 'current', 'overall', null, 1, 1), data)
      invalidate()
    },
  })
}

export function useRollback(id: string) {
  const invalidate = useInvalidateNeed(id)
  return useMutation({
    mutationFn: (n: number) =>
      unwrap(api.POST('/api/needs/{need_id}/rollback', { params: { path: { need_id: id } }, body: { n } })),
    onSuccess: invalidate,
  })
}

export function usePin(id: string) {
  const invalidate = useInvalidateNeed(id)
  return useMutation({
    mutationFn: ({ ref, pinned }: { ref: string; pinned: boolean }) =>
      pinned
        ? unwrap(api.DELETE('/api/needs/{need_id}/shortlist/{ref}', { params: { path: { need_id: id, ref } } }))
        : unwrap(api.PUT('/api/needs/{need_id}/shortlist/{ref}', { params: { path: { need_id: id, ref } } })),
    onSuccess: invalidate,
  })
}

// --------------------------------------------------------------------------- приглашения

const KANBAN_PAGE = 50

/** Воронка постранично (курсор): первая страница и «Показать ещё»; счётчики колонок и воронка приходят по всем приглашениям. */
export function useKanban(needId?: string | null) {
  return useInfiniteQuery({
    queryKey: [...qk.invitations, needId ?? 'all'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/invitations', {
          params: { query: { need_id: needId ?? undefined, limit: KANBAN_PAGE, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // Страницы склеиваются в одну воронку: потребители видят прежнюю форму `Kanban`
    select: (data): Kanban => {
      const [first, ...rest] = data.pages
      return {
        ...first,
        next_cursor: data.pages[data.pages.length - 1].next_cursor,
        columns: first.columns.map((col, i) => ({ ...col, items: [col, ...rest.map((p) => p.columns[i])].flatMap((c) => c.items) })),
      }
    },
  })
}

export function useInvalidateInvitations() {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: qk.invitations })
    void qc.invalidateQueries({ queryKey: qk.company })
    void qc.invalidateQueries({ queryKey: ['employer', 'selection'] })
    void qc.invalidateQueries({ queryKey: ['employer', 'bank'] })
  }
}

// --------------------------------------------------------------------------- вакансии, отклики, задачи

export function useVacancies() {
  return useQuery({
    queryKey: qk.vacancies,
    queryFn: () => unwrap(api.GET('/api/vacancies/mine')),
  })
}

export function useApplications(vacancyId?: string | null) {
  return useQuery({
    queryKey: [...qk.applications, vacancyId ?? 'all'],
    queryFn: () => unwrap(api.GET('/api/applications', { params: { query: { vacancy_id: vacancyId ?? undefined } } })),
  })
}

export function useTasks() {
  return useQuery({
    queryKey: qk.tasks,
    queryFn: () => unwrap(api.GET('/api/tasks')),
  })
}

// --------------------------------------------------------------------------- приглашения: карточка и действия

export function useInvitation(id: string | null, enabled = true) {
  return useQuery({
    queryKey: qk.invitation(id ?? ''),
    enabled: !!id && enabled,
    queryFn: () => unwrap(api.GET('/api/invitations/{invitation_id}', { params: { path: { invitation_id: id as string } } })),
  })
}

export type InvitationAction =
  | { kind: 'accept_counter' }
  | { kind: 'decline_counter'; reason: string; comment?: string }
  | { kind: 'withdraw'; reason: string; comment?: string }
  | { kind: 'contacted' }
  | { kind: 'interview' }
  | { kind: 'hired' }
  | { kind: 'not_matched'; reason: string; comment?: string; skillIds?: string[] }

/** Действия работодателя над приглашением: каждое возвращает свежую карточку и сбрасывает воронку. */
export function useInvitationAction() {
  const invalidate = useInvalidateInvitations()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: InvitationAction }) => {
      const path = { params: { path: { invitation_id: id } } }
      switch (action.kind) {
        case 'accept_counter':
          return unwrap(api.POST('/api/invitations/{invitation_id}/counter/accept', path))
        case 'decline_counter':
          return unwrap(
            api.POST('/api/invitations/{invitation_id}/counter/decline', { ...path, body: { reason: action.reason, comment: action.comment || null } }),
          )
        case 'withdraw':
          return unwrap(
            api.POST('/api/invitations/{invitation_id}/withdraw', { ...path, body: { reason: action.reason, comment: action.comment || null } }),
          )
        case 'contacted':
          return unwrap(api.POST('/api/invitations/{invitation_id}/contacted', path))
        case 'interview':
          return unwrap(api.POST('/api/invitations/{invitation_id}/interview', path))
        case 'hired':
          return unwrap(api.POST('/api/invitations/{invitation_id}/outcome', { ...path, body: { outcome: 'hired', skill_ids: [] } }))
        case 'not_matched':
          return unwrap(
            api.POST('/api/invitations/{invitation_id}/outcome', {
              ...path,
              body: {
                outcome: 'not_matched',
                reason: action.reason,
                skill_ids: action.skillIds ?? [],
                comment: action.comment || null,
              },
            }),
          )
      }
    },
    onSuccess: (data, vars) => {
      qc.setQueryData(qk.invitation(vars.id), data)
      invalidate()
    },
  })
}

// --------------------------------------------------------------------------- банк кандидатов

export interface BankParams {
  spec?: string | null
  grade?: string | null
  category?: string | null
  skills?: string[]
  /** Стек: только подтверждённые (по умолчанию бэкенда — да) */
  confirmedOnly?: boolean
  hasFsp?: boolean | null
  industry?: string | null
  searchMode?: string | null
  page?: number
  hiddenPage?: number
  needId?: string | null
  /** Только отметившие интерес (видны лишь по открытым карточкам) */
  interest?: boolean | null
  interestFirst?: boolean
}

type Grade = 'intern' | 'junior' | 'middle' | 'senior'

export function bankQuery(p: BankParams) {
  return {
    spec: p.spec || undefined,
    grade: (p.grade || undefined) as Grade | undefined,
    category: p.category || undefined,
    skill: p.skills && p.skills.length > 0 ? p.skills : undefined,
    confirmed_only: p.confirmedOnly,
    has_fsp: p.hasFsp ?? undefined,
    industry: p.industry || undefined,
    search_mode: p.searchMode || undefined,
    page: p.page,
    hidden_page: p.hiddenPage,
    need_id: p.needId || undefined,
    interest: p.interest ?? undefined,
    interest_first: p.interestFirst || undefined,
  }
}

export function useBank(p: BankParams, enabled = true) {
  return useQuery({
    queryKey: [...qk.bank, 'list', JSON.stringify(p)],
    enabled,
    placeholderData: keepPreviousData,
    queryFn: () => unwrap(api.GET('/api/bank', { params: { query: bankQuery(p) } })),
  })
}

export function useBankCard(refId: string | null, enabled = true) {
  return useQuery({
    queryKey: qk.bankCard(refId ?? ''),
    enabled: !!refId && enabled,
    queryFn: () => unwrap(api.GET('/api/bank/candidates/{ref}', { params: { path: { ref: refId as string } } })),
  })
}

export interface MapCell {
  spec: string
  grade: string
  n: number
  withFsp: number
  /** P25/P50/P75 ожиданий «на руки», ₽/мес; null, пока кандидатов с минимумом меньше порога приватности */
  salary: Schemas['SalaryQuantilesOut'] | null
}

export interface MapSpec {
  spec: string
  name: string
  /** Есть собственный тест или собирается из двух квалификаций (Fullstack) — строка карты с ячейками */
  tested: boolean
  n: number
  /** «Ждут теста»: прошли опрос по направлению, подтверждённой категории ещё нет */
  surveyOnly: number
}

/**
 * Данные карты талантов одним запросом GET /api/bank/summary (специализация × грейд: N, ФСП, ожидания, ждут теста).
 * Раньше карта делала ~14 запросов GET /api/bank и упиралась в лимит 30 в минуту; у сводки лимит свой.
 */
export function useTalentMap() {
  return useQuery({
    queryKey: [...qk.bank, 'summary'],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const d = await unwrap(api.GET('/api/bank/summary'))
      const specs: MapSpec[] = d.specs.map((s) => ({
        spec: s.spec,
        name: s.name,
        // направление с тестом, собираемое Fullstack или уже с кандидатами (так подхватываются DevOps и QA после запуска теста)
        tested: s.testable || s.status === 'derived' || s.n > 0,
        n: s.n,
        surveyOnly: s.survey_only,
      }))
      const cells: MapCell[] = d.specs.flatMap((s) =>
        s.cells.map((c) => ({ spec: s.spec, grade: c.grade, n: c.n, withFsp: c.with_fsp, salary: c.salary_min })),
      )
      return { specs, cells, total: d.total, withFsp: d.with_fsp, surveyOnly: d.survey_only, privacyMinN: d.privacy_min_n, fetchedAt: Date.now() }
    },
  })
}

export type SkillDict = Schemas['SkillOut']

/** Справочник навыков для фильтра «стек» в банке (GET /api/skills): по умолчанию только проверяемые тестом. */
export function useSkills(testable = true) {
  return useQuery({
    queryKey: ['employer', 'skills', testable] as const,
    staleTime: 60 * 60_000,
    queryFn: () => unwrap(api.GET('/api/skills', { params: { query: { testable } } })),
  })
}

// --------------------------------------------------------------------------- вакансии и отклики

export type VacancyCreate = Schemas['VacancyCreateIn']
export type VacancyPatch = Schemas['VacancyPatchIn']

export function useVacancyMutations() {
  const qc = useQueryClient()
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.vacancies })
    void qc.invalidateQueries({ queryKey: qk.applications })
  }
  const publish = useMutation({
    mutationFn: (body: VacancyCreate) => unwrap(api.POST('/api/vacancies', { body })),
    onSuccess: refresh,
  })
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: VacancyPatch }) =>
      unwrap(api.PATCH('/api/vacancies/{vacancy_id}', { params: { path: { vacancy_id: id } }, body })),
    onSuccess: refresh,
  })
  const close = useMutation({
    mutationFn: (id: string) => unwrap(api.POST('/api/vacancies/{vacancy_id}/close', { params: { path: { vacancy_id: id } } })),
    onSuccess: refresh,
  })
  return { publish, patch, close }
}

/** Карточка отклика: открытие отмечает его просмотренным — ответить можно только после просмотра. */
export function useApplication(id: string | null) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: ['employer', 'application', id ?? ''],
    enabled: !!id,
    queryFn: async () => {
      const d = await unwrap(api.GET('/api/applications/{application_id}', { params: { path: { application_id: id as string } } }))
      void qc.invalidateQueries({ queryKey: qk.applications })
      return d
    },
  })
}

export type ApplicationAction = { kind: 'interest' } | { kind: 'reject'; reason: string; comment?: string; skillIds?: string[] }

export function useApplicationAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: ApplicationAction }) => {
      const path = { params: { path: { application_id: id } } }
      if (action.kind === 'interest') return unwrap(api.POST('/api/applications/{application_id}/interest', path))
      return unwrap(
        api.POST('/api/applications/{application_id}/reject', {
          ...path,
          body: { reason: action.reason, comment: action.comment || null, skill_ids: action.skillIds ?? [] },
        }),
      )
    },
    onSuccess: (data, vars) => {
      qc.setQueryData(['employer', 'application', vars.id], data)
      void qc.invalidateQueries({ queryKey: qk.applications })
      void qc.invalidateQueries({ queryKey: qk.vacancies })
    },
  })
}

// --------------------------------------------------------------------------- регулярные задачи

export type TaskCreate = Schemas['TaskCreateIn']
export type CatalogFamily = Schemas['FamilyOut']
export type Submission = Schemas['SubmissionOut']

export function useTaskCatalog(specialization?: string | null) {
  return useQuery({
    queryKey: [...qk.catalog, specialization ?? 'all'],
    queryFn: () => unwrap(api.GET('/api/tasks/catalog', { params: { query: { specialization: specialization || undefined } } })),
    staleTime: 10 * 60_000,
  })
}

export function useTaskMutations() {
  const qc = useQueryClient()
  const refresh = () => void qc.invalidateQueries({ queryKey: qk.tasks })
  const create = useMutation({ mutationFn: (body: TaskCreate) => unwrap(api.POST('/api/tasks', { body })), onSuccess: refresh })
  const status = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'activate' | 'close' }) => {
      const path = { params: { path: { task_id: id } } }
      if (action === 'pause') return unwrap(api.POST('/api/tasks/{task_id}/pause', path))
      if (action === 'activate') return unwrap(api.POST('/api/tasks/{task_id}/activate', path))
      return unwrap(api.POST('/api/tasks/{task_id}/close', path))
    },
    onSuccess: refresh,
  })
  const review = useMutation({
    mutationFn: ({ id, scores, comment }: { id: string; scores: Record<string, number>; comment?: string }) =>
      unwrap(
        api.POST('/api/tasks/assignments/{assignment_id}/review', {
          params: { path: { assignment_id: id } },
          body: { scores, comment: comment || null },
        }),
      ),
    onSuccess: (_d, v) => {
      refresh()
      void qc.invalidateQueries({ queryKey: ['employer', 'task-submissions'] })
      return v
    },
  })
  return { create, status, review }
}

export function useTaskSubmissions(taskId: string | null) {
  return useQuery({
    queryKey: ['employer', 'task-submissions', taskId ?? ''],
    enabled: !!taskId,
    queryFn: () => unwrap(api.GET('/api/tasks/{task_id}/submissions', { params: { path: { task_id: taskId as string } } })),
  })
}
