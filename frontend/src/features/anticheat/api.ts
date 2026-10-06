import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { Schemas } from '@/api/types'

// Запросы «Антиподсказчика»: лаборатория (сверки, акт, подтверждение, покрытие, отчёт валидации) и песочница жюри.

export type ReconciliationRow = Schemas['ReconciliationRowOut']
export type ReconciliationList = Schemas['ReconciliationListOut']
export type Act = Schemas['ActOut']
export type ActLine = Schemas['ActLineOut']
export type ActStat = Schemas['ActStatOut']
export type ReviewAction = Schemas['ReconciliationReviewIn']['action']
export type LabKit = Schemas['LabVerificationKitOut']
export type TwinCoverage = Schemas['TwinCoverageOut']
export type TwinCoverageFamily = Schemas['TwinCoverageFamilyOut']
export type ValidationReport = Schemas['ValidationReportOut']
export type SandboxStart = Schemas['SandboxStartOut']
export type SandboxItem = Schemas['SandboxItemOut']
export type SandboxResult = Schemas['SandboxResultOut']
export type SandboxDiagnosis = Schemas['SandboxDiagnosisOut']

export type ReviewFilter = 'open' | 'verification_assigned' | 'dismissed' | 'all'

export const acKeys = {
  list: (status: ReviewFilter, sessionId?: string) => ['lab', 'reconciliations', status, sessionId ?? ''] as const,
  all: ['lab', 'reconciliations'] as const,
  act: (id: string) => ['lab', 'reconciliation', id] as const,
  kit: (sessionId: string) => ['lab', 'kit', sessionId] as const,
  coverage: ['lab', 'twins-coverage'] as const,
  report: ['lab', 'validation-report'] as const,
}

export function useReconciliations(status: ReviewFilter, sessionId?: string) {
  return useQuery({
    queryKey: acKeys.list(status, sessionId),
    queryFn: () =>
      unwrap(
        api.GET('/api/lab/reconciliations', {
          params: {
            query: {
              ...(status === 'all' ? {} : { status }),
              ...(sessionId ? { session_id: sessionId } : {}),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useAct(id: string) {
  return useQuery({
    queryKey: acKeys.act(id),
    queryFn: () => unwrap(api.GET('/api/lab/reconciliations/{reconciliation_id}', { params: { path: { reconciliation_id: id } } })),
  })
}

export function useReview(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { action: ReviewAction; note?: string }) =>
      unwrap(
        api.POST('/api/lab/reconciliations/{reconciliation_id}/review', {
          params: { path: { reconciliation_id: id } },
          body: { action: v.action, note: v.note?.trim() ? v.note.trim() : null },
        }),
      ),
    onSuccess: (act) => {
      qc.setQueryData(acKeys.act(id), act)
      void qc.invalidateQueries({ queryKey: acKeys.all })
    },
  })
}

export function useLabKit(sessionId: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: acKeys.kit(sessionId ?? ''),
    queryFn: () =>
      unwrap(api.GET('/api/lab/sessions/{session_id}/verification-kit', { params: { path: { session_id: sessionId ?? '' } } })),
    enabled: enabled && !!sessionId,
    retry: false,
    staleTime: 5 * 60_000,
  })
}

export function useTwinCoverage() {
  return useQuery({
    queryKey: acKeys.coverage,
    queryFn: () => unwrap(api.GET('/api/lab/twins/coverage')),
    // пока идёт расчёт в фоне — обновляем раз в несколько секунд
    refetchInterval: (q) => (q.state.data?.totals.running ? 4000 : false),
  })
}

export function usePrecompute() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { familyId?: string; perFamily?: number }) =>
      unwrap(
        api.POST('/api/lab/twins/precompute', {
          body: { family_id: v.familyId ?? null, per_family: v.perFamily ?? null },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: acKeys.coverage }),
  })
}

export function useValidationReport() {
  return useQuery({
    queryKey: acKeys.report,
    queryFn: () => unwrap(api.GET('/api/lab/validation/report')),
    retry: false,
    staleTime: 60_000,
  })
}

// ---- песочница жюри (публичная, только DEMO_MODE) ----

export function startSandbox() {
  return unwrap(api.POST('/api/jury/cheat-lab'))
}

export function submitSandbox(testId: string, answers: Record<string, string>, copied: number[]) {
  return unwrap(
    api.POST('/api/jury/cheat-lab/{test_id}/answers', {
      params: { path: { test_id: testId } },
      body: { answers, copied },
    }),
  )
}
