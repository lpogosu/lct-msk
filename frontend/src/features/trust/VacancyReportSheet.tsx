import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import { Sheet } from '@/features/candidate/components/Sheet'
import { cn } from '@/lib/cn'
import { Alert, Button, TextArea } from '@/ui'
import { useReportVacancy } from './api'

/** «Пожаловаться на вакансию»: причины приходят с бэкенда (config/contact.yaml → reasons.complaint). Компания о жалобе не узнает. */
export function VacancyReportSheet({
  open,
  onClose,
  vacancyId,
  reasons,
}: {
  open: boolean
  onClose: () => void
  vacancyId: string
  reasons: Record<string, string>
}) {
  const report = useReportVacancy(vacancyId)
  const [reason, setReason] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Пожаловаться на вакансию"
      description="Компания о жалобе не узнает. Жалоба попадёт к модератору платформы."
    >
      <div className="flex flex-col gap-5">
        <div role="radiogroup" aria-label="Причина жалобы" className="flex flex-col gap-2">
          {Object.entries(reasons).map(([code, label]) => (
            <button
              key={code}
              type="button"
              role="radio"
              aria-checked={reason === code}
              onClick={() => setReason(code)}
              className={cn(
                'rounded-panel-lg border px-4 py-3 text-left t-body transition-colors',
                reason === code ? 'border-brand bg-tint' : 'border-hairline bg-white hover:border-steel',
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <TextArea label="Комментарий (необязательно)" value={comment} onChange={(e) => setComment(e.target.value.slice(0, 300))} rows={3} hint={`${comment.length}/300`} />
        {report.error ? <Alert tone="danger">{errorMessage(report.error)}</Alert> : null}
        <Button
          block
          size="lg"
          disabled={!reason}
          loading={report.isPending}
          onClick={() => reason && report.mutate({ reason, comment: comment.trim() || undefined }, { onSuccess: onClose })}
        >
          Отправить жалобу
        </Button>
      </div>
    </Sheet>
  )
}
