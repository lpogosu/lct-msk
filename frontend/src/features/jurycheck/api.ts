import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { toApiException } from '@/api/errors'
import type { Schemas } from '@/api/types'

// Страница «Проверка подбора на ваших данных» (/lab/check): backend/app/api/admin.py → /api/admin/check/*, импорт и
// ранжирование. Доступно методисту (вход жюри «Лаборатория») и администратору; методист видит только свои загрузки.

export type CheckSummary = Schemas['CheckSummaryOut']
export type CheckBriefRow = Schemas['CheckBriefRowOut']
export type CheckBrief = Schemas['CheckBriefOut']
export type CheckRow = Schemas['CheckRowOut']
export type MetricValue = Schemas['MetricOut']
export type RankResult = Schemas['RankOut']
export type RankRow = Schemas['RankRowOut']
export type Batch = Schemas['BatchOut']
export type ImportResult = Schemas['ImportOut'] & { stats?: Record<string, number> }
export type SimpleImportBody = Schemas['SimpleImportIn']
export type RankBody = Schemas['RankIn']
export type TemplateName = 'vacancies.csv' | 'candidates.csv' | 'labels.csv' | 'example.json'

/** Источник сводки: набор пар из репозитория или своя загрузка. */
export type Source = { kind: 'repo' } | { kind: 'batch'; batchId: string }

export const checkKeys = {
  summary: (s: Source, gold: boolean) => ['lab', 'check', 'summary', s.kind === 'repo' ? 'repo' : s.batchId, gold] as const,
  brief: (s: Source, briefId: string, gold: boolean) =>
    ['lab', 'check', 'brief', s.kind === 'repo' ? 'repo' : s.batchId, briefId, gold] as const,
  imports: ['lab', 'check', 'imports'] as const,
}

export function useCheckSummary(source: Source | null, useGold: boolean) {
  return useQuery({
    queryKey: source ? checkKeys.summary(source, useGold) : ['lab', 'check', 'summary', 'none'],
    enabled: source !== null,
    queryFn: () => {
      const query = { use_gold: useGold }
      if (!source || source.kind === 'repo') return unwrap(api.GET('/api/admin/check/repo', { params: { query } }))
      return unwrap(api.GET('/api/admin/check/batches/{batch_id}', { params: { path: { batch_id: source.batchId }, query } }))
    },
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  })
}

export function useCheckBrief(source: Source | null, briefId: string | null, useGold: boolean) {
  return useQuery({
    queryKey: source && briefId ? checkKeys.brief(source, briefId, useGold) : ['lab', 'check', 'brief', 'none'],
    enabled: source !== null && briefId !== null,
    queryFn: () => {
      const query = { use_gold: useGold }
      const id = briefId ?? ''
      if (!source || source.kind === 'repo') {
        return unwrap(api.GET('/api/admin/check/repo/briefs/{brief_id}', { params: { path: { brief_id: id }, query } }))
      }
      return unwrap(
        api.GET('/api/admin/check/batches/{batch_id}/briefs/{brief_id}', {
          params: { path: { batch_id: source.batchId, brief_id: id }, query },
        }),
      )
    },
    retry: false,
    staleTime: 60_000,
  })
}

export function useImports() {
  return useQuery({ queryKey: checkKeys.imports, queryFn: () => unwrap(api.GET('/api/admin/import')), retry: false })
}

export function useImportPairs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { name: string; pairs: Record<string, unknown>[] }) =>
      unwrap(api.POST('/api/admin/import', { body: { name: v.name, pairs: v.pairs } })) as Promise<ImportResult>,
    onSuccess: () => void qc.invalidateQueries({ queryKey: checkKeys.imports }),
  })
}

export function useImportSimple() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SimpleImportBody) => unwrap(api.POST('/api/admin/import/simple', { body })) as Promise<ImportResult>,
    onSuccess: () => void qc.invalidateQueries({ queryKey: checkKeys.imports }),
  })
}

export function useDeleteImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE('/api/admin/import/{batch_id}', { params: { path: { batch_id: id } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: checkKeys.imports }),
  })
}

/** «Своя вакансия против банка платформы»: та же подборка, что у работодателя, кандидаты — под псевдонимами. */
export function useRankPlatform() {
  return useMutation({
    mutationFn: (body: Pick<RankBody, 'text' | 'salary_from' | 'salary_to' | 'salary_net' | 'top'>) =>
      unwrap(
        api.POST('/api/admin/eval/rank', {
          body: { ...body, source: 'platform', use_gold: false, all_categories: false },
        }),
      ),
  })
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** Шаблон простого формата (с примером на 3 вакансии). */
export async function downloadTemplate(name: TemplateName): Promise<void> {
  const res = await api.GET('/api/admin/check/templates/{name}', { params: { path: { name } }, parseAs: 'blob' })
  if (res.error !== undefined || !res.response.ok) throw toApiException(res.response.status, res.error)
  saveBlob(res.data as Blob, name)
}

/** CSV топов по всем брифам набора. */
export async function downloadTops(source: Source, useGold: boolean): Promise<void> {
  const query = { use_gold: useGold }
  const res =
    source.kind === 'repo'
      ? await api.GET('/api/admin/check/repo/tops.csv', { params: { query }, parseAs: 'blob' })
      : await api.GET('/api/admin/check/batches/{batch_id}/tops.csv', {
          params: { path: { batch_id: source.batchId }, query },
          parseAs: 'blob',
        })
  if (res.error !== undefined || !res.response.ok) throw toApiException(res.response.status, res.error)
  saveBlob(res.data as Blob, source.kind === 'repo' ? 'tops-pairs_test.csv' : `tops-${source.batchId.slice(0, 8)}.csv`)
}
