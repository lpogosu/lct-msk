import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { qk, type InvitationDetail } from '@/features/candidate/queries'

/**
 * Действия кандидата над приглашением. Каждое возвращает обновлённую карточку (`InvitationCandidateDetail`):
 * кладём её в кэш и перечитываем список, а согласия — потому что «да» и отзыв пишут в журнал.
 */
export function useInvitationActions(id: string) {
  const qc = useQueryClient()
  const path = { params: { path: { invitation_id: id } } } as const
  const onSuccess = (d: InvitationDetail) => {
    qc.setQueryData(qk.invitation(id), d)
    void qc.invalidateQueries({ queryKey: qk.inbox })
    void qc.invalidateQueries({ queryKey: qk.consents })
  }

  const accept = useMutation({
    mutationFn: (v: { fields: string[] }) =>
      unwrap(api.POST('/api/invitations/{invitation_id}/accept', { ...path, body: { consent: true, fields: v.fields } })),
    onSuccess,
  })
  const counter = useMutation({
    mutationFn: (v: { salary_from?: number; format?: string; start_date?: string; comment?: string; auto_disclose: boolean }) =>
      unwrap(api.POST('/api/invitations/{invitation_id}/counter', { ...path, body: v })),
    onSuccess,
  })
  const decline = useMutation({
    mutationFn: (v: { reason: string; comment?: string }) =>
      unwrap(api.POST('/api/invitations/{invitation_id}/decline', { ...path, body: v })),
    onSuccess,
  })
  const dispute = useMutation({
    mutationFn: (v: { comment?: string }) => unwrap(api.POST('/api/invitations/{invitation_id}/dispute', { ...path, body: v })),
    onSuccess,
  })
  const check = useMutation({
    mutationFn: (v: { answer: string }) => unwrap(api.POST('/api/invitations/{invitation_id}/check', { ...path, body: v })),
    onSuccess,
  })
  const revoke = useMutation({
    mutationFn: () => unwrap(api.POST('/api/invitations/{invitation_id}/revoke-consent', path)),
    onSuccess,
  })
  const grant = useMutation({
    mutationFn: (v: { fields: string[] }) =>
      unwrap(api.POST('/api/invitations/{invitation_id}/grant-consent', { ...path, body: { consent: true, fields: v.fields } })),
    onSuccess,
  })
  const report = useMutation({
    mutationFn: (v: { reason: string; comment?: string }) => unwrap(api.POST('/api/invitations/{invitation_id}/report', { ...path, body: v })),
    onSuccess,
  })
  return { accept, counter, decline, dispute, check, revoke, grant, report }
}
