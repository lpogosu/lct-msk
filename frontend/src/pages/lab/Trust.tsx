import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import { CompanyTrustBadge } from '@/features/trust/CompanyTrustBadge'
import { useComplaints, useFlags, useResolveComplaint, useRevokeVerification, type Complaint } from '@/features/trust/api'
import { formatDate } from '@/lib/format'
import { Alert, Button, EmptyState, Segmented, TextArea, useToast } from '@/ui'
import { Chip, LabError, LabHead, LabSkeleton, Table, Td } from './parts'

type View = 'open' | 'upheld' | 'dismissed' | 'flags'

const VIEWS = [
  { value: 'open', label: 'Ожидают разбора' },
  { value: 'upheld', label: 'Подтверждены' },
  { value: 'dismissed', label: 'Отклонены' },
  { value: 'flags', label: 'Автосигналы' },
] as const

/** Модерация доверия: очередь жалоб кандидатов, автосигналы по компаниям, отзыв метки «Проверенная компания». */
export default function LabTrust() {
  const [view, setView] = useState<View>('open')
  return (
    <div className="flex flex-col gap-8">
      <LabHead
        caption="Доверие и безопасность"
        title="Жалобы и сигналы"
        lead="Кандидаты жалуются на приглашения и вакансии: сбор резюме без вакансии, не та зарплата, молчание после «да». Три жалобы от разных кандидатов снижают квоту компании; подтверждённая серьёзная жалоба снимает метку «Проверенная компания». Автосигналы только подсказывают."
      />
      <Segmented aria-label="Раздел" options={VIEWS} value={view} onChange={setView} />
      {view === 'flags' ? <Flags /> : <Queue status={view} />}
    </div>
  )
}

function Queue({ status }: { status: 'open' | 'upheld' | 'dismissed' }) {
  const q = useComplaints(status)
  if (q.isPending) return <LabSkeleton />
  if (q.isError) return <LabError error={q.error} onRetry={() => void q.refetch()} />
  if (q.data.length === 0) return <EmptyState title="Жалоб нет" description="В этом разделе пока пусто." />
  return (
    <Table head={['Компания', 'Объект', 'Причина', 'Комментарий кандидата', 'Дата', status === 'open' ? 'Решение' : 'Итог']}>
      {q.data.map((c) => (
        <ComplaintRow key={c.id} c={c} />
      ))}
    </Table>
  )
}

function ComplaintRow({ c }: { c: Complaint }) {
  const resolve = useResolveComplaint()
  const toast = useToast()
  const [note, setNote] = useState('')
  const open = c.status === 'open'

  async function decide(decision: 'upheld' | 'dismissed') {
    try {
      await resolve.mutateAsync({ id: c.id, decision, note: note.trim() || undefined })
      toast.success(decision === 'upheld' ? 'Жалоба подтверждена.' : 'Жалоба отклонена.')
    } catch {
      /* ошибку показывает resolve.error */
    }
  }

  return (
    <tr className="border-b border-hairline last:border-0">
      <Td>
        <p className="t-body-strong text-ink [overflow-wrap:anywhere]">{c.company_name}</p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <Chip tone={c.company_open_reporters >= 3 ? 'danger' : 'neutral'}>жалоб от разных кандидатов: {c.company_open_reporters}</Chip>
          <CompanyTrustBadge
            trust={{ level: c.verification_level as 'none' | 'requisites' | 'domain', label: 'Проверенная компания', hint: null }}
          />
        </div>
      </Td>
      <Td>
        {c.target_type === 'vacancy' ? 'Вакансия' : 'Приглашение'}
        {c.target_title ? <span className="block t-small text-ink-3 [overflow-wrap:anywhere]">{c.target_title}</span> : null}
      </Td>
      <Td>{c.reason_label}</Td>
      <Td className="max-w-[260px] [overflow-wrap:anywhere]">{c.comment ?? <span className="text-ink-3">—</span>}</Td>
      <Td className="whitespace-nowrap">{formatDate(c.created_at)}</Td>
      <Td className="min-w-[240px]">
        {open ? (
          <div className="flex flex-col gap-2">
            <TextArea
              aria-label="Заметка модератора"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value.slice(0, 300))}
              placeholder="Заметка (необязательно)"
            />
            {resolve.error ? <Alert tone="danger">{errorMessage(resolve.error)}</Alert> : null}
            <div className="flex gap-2">
              <Button size="sm" loading={resolve.isPending} onClick={() => void decide('upheld')}>
                Подтвердить
              </Button>
              <Button size="sm" variant="outline" loading={resolve.isPending} onClick={() => void decide('dismissed')}>
                Отклонить
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Chip tone={c.status === 'upheld' ? 'danger' : 'neutral'}>{c.status_label}</Chip>
            {c.resolution_note ? <p className="mt-1 t-small text-ink-3">{c.resolution_note}</p> : null}
          </div>
        )}
      </Td>
    </tr>
  )
}

function Flags() {
  const q = useFlags()
  const revoke = useRevokeVerification()
  const toast = useToast()
  if (q.isPending) return <LabSkeleton />
  if (q.isError) return <LabError error={q.error} onRetry={() => void q.refetch()} />
  if (q.data.length === 0) return <EmptyState title="Сигналов нет" description="Ни у одной компании не сработали эвристики." />
  return (
    <ul className="m-0 flex list-none flex-col gap-4 p-0">
      {q.data.map((co) => (
        <li key={co.company_id} className="rounded-panel-lg border border-hairline p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="t-body-strong text-ink [overflow-wrap:anywhere]">{co.company_name}</p>
            {co.verification_level !== 'none' && (
              <Button
                size="sm"
                variant="outline"
                loading={revoke.isPending}
                onClick={() =>
                  revoke.mutate(
                    { companyId: co.company_id, reason: 'other', note: 'Снято по автосигналам' },
                    { onSuccess: () => toast.success('Метка отозвана.') },
                  )
                }
              >
                Отозвать метку
              </Button>
            )}
          </div>
          <ul className="mt-3 flex list-none flex-col gap-1.5 p-0">
            {co.flags.map((f) => (
              <li key={f.code} className="t-body text-ink-2">
                <span className="text-ink">{f.label}.</span> {f.detail}
              </li>
            ))}
          </ul>
        </li>
      ))}
      {revoke.error ? <Alert tone="danger">{errorMessage(revoke.error)}</Alert> : null}
    </ul>
  )
}
