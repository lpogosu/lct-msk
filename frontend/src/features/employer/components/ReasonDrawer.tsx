import { useState } from 'react'
import { Alert, Button, Checkbox, Segmented, TextArea } from '@/ui'
import { Drawer } from '@/ui/Drawer'

export interface ReasonOption {
  code: string
  label: string
}

export interface ReasonResult {
  reason: string
  comment: string
  skillIds: string[]
}

const COMMENT_MAX = 300

/**
 * Окно «причина обязательна»: отказ на встречное условие, отзыв приглашения, итог «не сложилось», отказ отклику.
 * Причина уходит кандидату — поэтому в комментарии нельзя оставлять контакты и ссылки (до «да» их проверяет бэкенд).
 */
export function ReasonDrawer({
  open,
  onClose,
  title,
  eyebrow,
  description,
  options,
  submitLabel,
  skills,
  skillsReason = 'skills_missing',
  busy,
  error,
  onSubmit,
}: {
  open: boolean
  onClose: () => void
  title: string
  eyebrow?: string
  description?: string
  options: readonly ReasonOption[]
  submitLabel: string
  /** Навыки брифа: показываем, когда выбрана причина «не хватило навыков» */
  skills?: { id: string; name: string }[]
  skillsReason?: string
  busy?: boolean
  error?: string | null
  onSubmit: (r: ReasonResult) => void
}) {
  const [reason, setReason] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [skillIds, setSkillIds] = useState<string[]>([])
  const [touched, setTouched] = useState(false)

  const needSkills = reason === skillsReason && (skills?.length ?? 0) > 0
  const missingSkills = needSkills && skillIds.length === 0

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={520}
      title={title}
      eyebrow={eyebrow}
      footer={
        <div className="flex flex-wrap items-center gap-4">
          <Button
            loading={busy}
            onClick={() => {
              setTouched(true)
              if (!reason || missingSkills) return
              onSubmit({ reason, comment: comment.trim(), skillIds })
            }}
          >
            {submitLabel}
          </Button>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        {description && <p className="t-body text-ink-2">{description}</p>}
        <div className="flex flex-col gap-2">
          <p id="reason-label" className="t-label text-ink-2">
            Причина
          </p>
          <Segmented
            aria-label="Причина"
            options={options.map((o) => ({ value: o.code, label: o.label }))}
            value={reason}
            onChange={setReason}
          />
          {touched && !reason && (
            <p role="alert" className="t-small text-danger-text">
              Выберите причину — её увидит кандидат.
            </p>
          )}
        </div>
        {needSkills && (
          <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
            <legend className="mb-1 t-label text-ink-2">Каких навыков не хватило</legend>
            {skills?.map((s) => (
              <Checkbox
                key={s.id}
                label={s.name}
                checked={skillIds.includes(s.id)}
                onChange={(e) => setSkillIds((cur) => (e.target.checked ? [...cur, s.id] : cur.filter((x) => x !== s.id)))}
              />
            ))}
            {touched && missingSkills && (
              <p role="alert" className="t-small text-danger-text">
                Отметьте хотя бы один навык: кандидат получит ссылку на короткую задачу по нему.
              </p>
            )}
          </fieldset>
        )}
        <TextArea
          label="Комментарий (необязательно)"
          rows={3}
          maxLength={COMMENT_MAX}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          hint={`${comment.length} из ${COMMENT_MAX}. Без контактов и ссылок — кандидат читает это до взаимного «да».`}
        />
        {error && (
          <Alert tone="danger" title="Не получилось">
            {error}
          </Alert>
        )}
      </div>
    </Drawer>
  )
}
