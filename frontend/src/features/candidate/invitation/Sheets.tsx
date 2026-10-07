import { useState, type ReactNode } from 'react'
import { errorMessage } from '@/api/errors'
import { DisclosureFields } from '@/features/candidate/components/DisclosureFields'
import { chosen, fieldOptions, hasContact } from '@/features/candidate/components/disclosureUtil'
import { Sheet } from '@/features/candidate/components/Sheet'
import { usePrivacy, type InvitationDetail } from '@/features/candidate/queries'
import { formatRub } from '@/lib/format'
import { cn } from '@/lib/cn'
import { Alert, Button, Checkbox, Input, Skeleton, TextArea, fieldBorderClasses } from '@/ui'
import type { useInvitationActions } from './actions'

type Actions = ReturnType<typeof useInvitationActions>

interface SheetProps {
  open: boolean
  onClose: () => void
  detail: InvitationDetail
  actions: Actions
}

const company = (d: InvitationDetail) => d.company?.name ?? 'компании'

// --- «Да»: отдельное согласие на передачу контактов (152-ФЗ) -------------------------------------------------------------

/** Общая форма согласия: выбор полей + галочка согласия именно этой компании. Используется для «Да» и для подтверждения после встречного. */
function ConsentForm({
  detail,
  submitLabel,
  pending,
  error,
  onSubmit,
}: {
  detail: InvitationDetail
  submitLabel: string
  pending: boolean
  error: unknown
  onSubmit: (fields: string[]) => void
}) {
  const privacy = usePrivacy()
  if (privacy.isPending) return <Skeleton className="h-40 w-full rounded-panel" />
  const { options, initial } = fieldOptions(privacy.data, detail.consent.default_fields)
  return <ConsentFormInner detail={detail} options={options} initial={initial} submitLabel={submitLabel} pending={pending} error={error} onSubmit={onSubmit} />
}

function ConsentFormInner({
  detail,
  options,
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
}: {
  detail: InvitationDetail
  options: ReturnType<typeof fieldOptions>['options']
  initial: Record<string, boolean>
  submitLabel: string
  pending: boolean
  error: unknown
  onSubmit: (fields: string[]) => void
}) {
  const [fields, setFields] = useState(initial)
  const [agree, setAgree] = useState(false)
  const contactOk = hasContact(fields)
  return (
    <div className="flex flex-col gap-5">
      <DisclosureFields options={options} value={fields} onChange={setFields} />
      <Checkbox
        checked={agree}
        onChange={(e) => setAgree(e.target.checked)}
        label={<span className="t-body-strong">Согласен передать выбранные данные компании «{company(detail)}»</span>}
        description={`Отдельное согласие на обработку персональных данных (версия текста ${detail.consent.text_version}). Его можно отозвать в любой момент: контакты снова скроются.`}
      />
      {!contactOk && <Alert tone="danger">Выберите хотя бы один способ связи — иначе компании будет нечем с вами связаться.</Alert>}
      {error ? <Alert tone="danger">{errorMessage(error)}</Alert> : null}
      <Button block size="lg" disabled={!agree || !contactOk} loading={pending} onClick={() => onSubmit(chosen(fields))}>
        {submitLabel}
      </Button>
    </div>
  )
}

export function AcceptSheet({ open, onClose, detail, actions }: SheetProps) {
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={`Да, интересно: передать контакты «${company(detail)}»?`}
      description="Имя и контакты компания увидит только после этого согласия. Пока вы не ответили, она их не видит."
    >
      <ConsentForm
        detail={detail}
        submitLabel="Да, интересно"
        pending={actions.accept.isPending}
        error={actions.accept.error}
        onSubmit={(fields) => actions.accept.mutate({ fields }, { onSuccess: onClose })}
      />
    </Sheet>
  )
}

/** Контакты открываются только после отдельного согласия, если во «Да, если…» автораскрытие было снято. */
export function GrantSheet({ open, onClose, detail, actions }: SheetProps) {
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Открыть контакты компании"
      description={`«${company(detail)}» приняла ваши условия. Контакты откроются после вашего согласия.`}
    >
      <ConsentForm
        detail={detail}
        submitLabel="Открыть контакты"
        pending={actions.grant.isPending}
        error={actions.grant.error}
        onSubmit={(fields) => actions.grant.mutate({ fields }, { onSuccess: onClose })}
      />
    </Sheet>
  )
}

// --- «Да, если…» --------------------------------------------------------------------------------------------------------

type CondKey = 'salary_from' | 'format' | 'start_date'

export function CounterSheet({ open, onClose, detail, actions, onDecline }: SheetProps & { onDecline: () => void }) {
  const lim = detail.counter_limits
  const privacy = usePrivacy()
  const [sel, setSel] = useState<Record<CondKey, boolean>>({ salary_from: true, format: false, start_date: false })
  const [salary, setSalary] = useState('')
  const [format, setFormat] = useState('remote')
  const [date, setDate] = useState('')
  const [comment, setComment] = useState('')
  const [auto, setAuto] = useState<boolean | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)

  if (!lim) {
    return (
      <Sheet open={open} onClose={onClose} title="Да, если…">
        <p className="t-body text-ink-2">Встречное условие уже предложено или недоступно для этого приглашения.</p>
      </Sheet>
    )
  }

  const max = lim.max_conditions
  const count = Object.values(sel).filter(Boolean).length
  const autoValue = auto ?? lim.auto_disclose_default
  const formats = privacy.data?.options?.formats ?? [
    { code: 'office', name: 'Офис' },
    { code: 'hybrid', name: 'Гибрид' },
    { code: 'remote', name: 'Удалённо' },
  ]
  const labels = lim.conditions

  const toggle = (k: CondKey, on: boolean) => {
    if (on && count >= max) return
    setSel({ ...sel, [k]: on })
  }

  function submit() {
    setLocalError(null)
    if (count === 0) return setLocalError('Выберите хотя бы одно условие.')
    const body: Parameters<Actions['counter']['mutate']>[0] = { auto_disclose: autoValue }
    if (sel.salary_from) {
      const n = Number(salary)
      if (!Number.isFinite(n) || n <= 0) return setLocalError('Укажите сумму, от которой вы готовы рассмотреть предложение.')
      if (n < lim!.salary_min || n > lim!.salary_max)
        return setLocalError(`Зарплата — от ${formatRub(lim!.salary_min)} до ${formatRub(lim!.salary_max)}.`)
      if (n % lim!.salary_step !== 0) return setLocalError(`Сумма — с шагом ${formatRub(lim!.salary_step)}.`)
      body.salary_from = n
    }
    if (sel.format) body.format = format
    if (sel.start_date) {
      if (!date) return setLocalError('Выберите дату выхода.')
      body.start_date = date
    }
    if (comment.trim()) body.comment = comment.trim()
    actions.counter.mutate(body, { onSuccess: onClose })
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Да, если…"
      description={`Предложите до ${max} условий: компания ответит за 3 дня. Встречное можно предложить один раз.`}
    >
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-4">
          <CondRow
            label={labels.salary_from ?? 'Зарплата от'}
            on={sel.salary_from}
            disabled={!sel.salary_from && count >= max}
            onToggle={(v) => toggle('salary_from', v)}
          >
            <Input
              aria-label="Зарплата от, ₽"
              type="number"
              inputMode="numeric"
              min={lim.salary_min}
              max={lim.salary_max}
              step={lim.salary_step}
              value={salary}
              onChange={(e) => setSalary(e.target.value)}
              placeholder={String(Math.max(lim.salary_min, 250_000))}
              hint={`От ${formatRub(lim.salary_min)} до ${formatRub(lim.salary_max)}, шаг ${formatRub(lim.salary_step)}${detail.terms?.salary_type_label ? ` · ${detail.terms.salary_type_label}` : ''}`}
              mono
            />
          </CondRow>
          <CondRow
            label={labels.format ?? 'Формат работы'}
            on={sel.format}
            disabled={!sel.format && count >= max}
            onToggle={(v) => toggle('format', v)}
          >
            <select
              aria-label="Формат работы"
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              className={cn(fieldBorderClasses(false), 'h-12 px-3.5 t-body')}
            >
              {formats.map((f) => (
                <option key={f.code} value={f.code}>
                  {f.name}
                </option>
              ))}
            </select>
          </CondRow>
          <CondRow
            label={labels.start_date ?? 'Дата выхода'}
            on={sel.start_date}
            disabled={!sel.start_date && count >= max}
            onToggle={(v) => toggle('start_date', v)}
          >
            <input
              aria-label="Дата выхода"
              type="date"
              value={date}
              min={new Date().toISOString().slice(0, 10)}
              onChange={(e) => setDate(e.target.value)}
              className={cn(fieldBorderClasses(false), 'h-12 px-3.5 t-body')}
            />
          </CondRow>
          {count >= max && <p className="t-small text-ink-3">Выбрано максимум условий ({max}). Снимите одно, чтобы выбрать другое.</p>}
        </div>

        <TextArea
          label="Комментарий (необязательно)"
          value={comment}
          onChange={(e) => setComment(e.target.value.slice(0, lim.comment_max_len))}
          rows={3}
          hint={`${comment.length}/${lim.comment_max_len}. Контакты и ссылки писать нельзя: компания не должна видеть их до вашего согласия.`}
        />

        <Checkbox
          checked={autoValue}
          onChange={(e) => setAuto(e.target.checked)}
          label={<span className="t-body-strong">Раскрыть контакты, если условия примут</span>}
          description="Если снять галочку, после согласия компании мы отдельно спросим, открывать ли контакты. Данные — как в настройках приватности."
        />

        {localError && <Alert tone="danger">{localError}</Alert>}
        {actions.counter.error ? <Alert tone="danger">{errorMessage(actions.counter.error)}</Alert> : null}

        <div className="flex flex-col gap-2">
          <Button block size="lg" loading={actions.counter.isPending} onClick={submit}>
            Отправить встречное условие
          </Button>
          <Button variant="text" className="self-center" onClick={onDecline}>
            Нет, лучше отказаться
          </Button>
        </div>
      </div>
    </Sheet>
  )
}

function CondRow({
  label,
  on,
  disabled,
  onToggle,
  children,
}: {
  label: string
  on: boolean
  disabled: boolean
  onToggle: (v: boolean) => void
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-2">
      <Checkbox checked={on} disabled={disabled} onChange={(e) => onToggle(e.target.checked)} label={<span className="t-body-strong">{label}</span>} />
      {on && <div className="pl-6">{children}</div>}
    </div>
  )
}

// --- «Нет» ---------------------------------------------------------------------------------------------------------------

export function DeclineSheet({ open, onClose, detail, actions, onCounter }: SheetProps & { onCounter: () => void }) {
  const [reason, setReason] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const picked = detail.decline_reasons.find((r) => r.code === reason)
  const canCounter = !!detail.counter_limits

  return (
    <Sheet open={open} onClose={onClose} title="Почему «нет»?" description="Причина обязательна: она уходит компании без вашего имени и помогает ей поправить вилку.">
      <div className="flex flex-col gap-5">
        <div role="radiogroup" aria-label="Причина отказа" className="flex flex-wrap gap-2.5">
          {detail.decline_reasons.map((r) => (
            <button
              key={r.code}
              type="button"
              role="radio"
              aria-checked={reason === r.code}
              onClick={() => setReason(r.code)}
              className={cn(
                'rounded-pill border px-4 py-2 t-label transition-colors',
                reason === r.code ? 'border-brand bg-brand text-white' : 'border-steel bg-white text-ink hover:border-brand hover:text-brand',
              )}
            >
              {r.label}
            </button>
          ))}
        </div>

        {picked?.suggest_counter && picked.hint && canCounter && (
          <Alert title="Может, дело в деньгах?">
            {picked.hint}
            <div className="mt-2">
              <Button variant="outline" size="sm" onClick={onCounter}>
                Ответить «Да, если…»
              </Button>
            </div>
          </Alert>
        )}

        <TextArea
          label="Комментарий (необязательно)"
          value={comment}
          onChange={(e) => setComment(e.target.value.slice(0, 300))}
          rows={3}
          hint={`${comment.length}/300. Контакты и ссылки писать нельзя.`}
        />
        {actions.decline.error ? <Alert tone="danger">{errorMessage(actions.decline.error)}</Alert> : null}
        <Button
          block
          size="lg"
          disabled={!reason}
          loading={actions.decline.isPending}
          onClick={() => reason && actions.decline.mutate({ reason, comment: comment.trim() || undefined }, { onSuccess: onClose })}
        >
          Отказаться
        </Button>
      </div>
    </Sheet>
  )
}

// --- Жалоба -------------------------------------------------------------------------------------------------------------

export function ReportSheet({ open, onClose, detail, actions }: SheetProps) {
  const [reason, setReason] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Пожаловаться на приглашение"
      description="Компания о жалобе не узнает. Счётчик жалоб виден в её профиле и модерации."
    >
      <div className="flex flex-col gap-5">
        <div role="radiogroup" aria-label="Причина жалобы" className="flex flex-col gap-2">
          {Object.entries(detail.complaint_reasons).map(([code, label]) => (
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
        {actions.report.error ? <Alert tone="danger">{errorMessage(actions.report.error)}</Alert> : null}
        <Button
          block
          size="lg"
          disabled={!reason}
          loading={actions.report.isPending}
          onClick={() => reason && actions.report.mutate({ reason, comment: comment.trim() || undefined }, { onSuccess: onClose })}
        >
          Отправить жалобу
        </Button>
      </div>
    </Sheet>
  )
}

// --- «Со мной не связались» и отзыв согласия -------------------------------------------------------------------------------

export function DisputeSheet({ open, onClose, actions }: Omit<SheetProps, 'detail'>) {
  const [comment, setComment] = useState('')
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Со мной не связались"
      description="Мы запишем это в надёжность компании: срок первого контакта — 3 рабочих дня после вашего «да»."
    >
      <div className="flex flex-col gap-5">
        <TextArea label="Комментарий (необязательно)" value={comment} onChange={(e) => setComment(e.target.value.slice(0, 300))} rows={3} hint={`${comment.length}/300`} />
        {actions.dispute.error ? <Alert tone="danger">{errorMessage(actions.dispute.error)}</Alert> : null}
        <Button block size="lg" loading={actions.dispute.isPending} onClick={() => actions.dispute.mutate({ comment: comment.trim() || undefined }, { onSuccess: onClose })}>
          Сообщить, что не связались
        </Button>
      </div>
    </Sheet>
  )
}

export function RevokeSheet({ open, onClose, detail, actions }: SheetProps) {
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Отозвать согласие на передачу контактов?"
      description={`Контакты у «${company(detail)}» снова скроются. Приглашение перейдёт в «Согласие отозвано», пауза пары — 14 дней.`}
    >
      <div className="flex flex-col gap-3">
        {actions.revoke.error ? <Alert tone="danger">{errorMessage(actions.revoke.error)}</Alert> : null}
        <Button block size="lg" variant="outline" loading={actions.revoke.isPending} onClick={() => actions.revoke.mutate(undefined, { onSuccess: onClose })}>
          Отозвать согласие
        </Button>
        <Button block variant="text" onClick={onClose}>
          Оставить как есть
        </Button>
      </div>
    </Sheet>
  )
}
