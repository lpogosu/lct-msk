import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { Schemas } from '@/api/types'

// Метка «Проверенная компания», согласие законного представителя, модерация жалоб.

export type Verification = Schemas['VerificationOut']
export type Guardian = Schemas['GuardianStatusOut']
export type Complaint = Schemas['ComplaintOut']
export type CompanyFlags = Schemas['CompanyFlagsOut']

export const trustKeys = {
  verification: ['employer', 'verification'] as const,
  guardian: ['candidate', 'guardian'] as const,
  complaints: (status: string) => ['lab', 'trust', 'complaints', status] as const,
  flags: ['lab', 'trust', 'flags'] as const,
}

export function useVerification() {
  return useQuery({ queryKey: trustKeys.verification, queryFn: () => unwrap(api.GET('/api/companies/me/verification')) })
}

export function useSubmitVerification() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { inn: string; ogrn: string }) => unwrap(api.POST('/api/companies/me/verification', { body })),
    onSuccess: (data) => {
      qc.setQueryData(trustKeys.verification, data)
      void qc.invalidateQueries({ queryKey: ['employer', 'company'] })
    },
  })
}

export function useWithdrawVerification() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/companies/me/verification')),
    onSuccess: (data) => {
      qc.setQueryData(trustKeys.verification, data)
      void qc.invalidateQueries({ queryKey: ['employer', 'company'] })
    },
  })
}

export function useGuardian() {
  return useQuery({ queryKey: trustKeys.guardian, queryFn: () => unwrap(api.GET('/api/candidates/me/guardian')) })
}

export function useRequestGuardian() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { guardian_name: string; guardian_email: string }) => unwrap(api.POST('/api/candidates/me/guardian', { body })),
    onSuccess: (data) => {
      qc.setQueryData(trustKeys.guardian, data)
      void qc.invalidateQueries({ queryKey: ['candidate', 'profile'] })
      void qc.invalidateQueries({ queryKey: ['candidate', 'privacy'] })
    },
  })
}

export function useReportVacancy(vacancyId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { reason: string; comment?: string }) =>
      unwrap(api.POST('/api/vacancies/{vacancy_id}/report', { params: { path: { vacancy_id: vacancyId } }, body })),
    onSuccess: (data) => qc.setQueryData(['candidate', 'vacancy', vacancyId], data),
  })
}

export function useComplaints(status: 'open' | 'upheld' | 'dismissed') {
  return useQuery({
    queryKey: trustKeys.complaints(status),
    queryFn: () => unwrap(api.GET('/api/admin/trust/complaints', { params: { query: { status } } })),
  })
}

export function useResolveComplaint() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; decision: 'upheld' | 'dismissed'; note?: string }) =>
      unwrap(
        api.POST('/api/admin/trust/complaints/{complaint_id}/resolve', {
          params: { path: { complaint_id: v.id } },
          body: { decision: v.decision, note: v.note },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['lab', 'trust'] }),
  })
}

export function useFlags() {
  return useQuery({ queryKey: trustKeys.flags, queryFn: () => unwrap(api.GET('/api/admin/trust/flags')) })
}

export function useRevokeVerification() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { companyId: string; reason: string; note?: string }) =>
      unwrap(
        api.POST('/api/admin/trust/companies/{company_id}/revoke', {
          params: { path: { company_id: v.companyId } },
          body: { reason: v.reason, note: v.note },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['lab', 'trust'] }),
  })
}
