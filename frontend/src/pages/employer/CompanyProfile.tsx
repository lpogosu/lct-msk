import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import { qk, useCompany, useSaveCompany, type CompanyMe, type CompanyProfileIn } from '@/features/employer/api'
import { BlockTitle, Label, LoadError, PageHead, PageSkeleton, Tag } from '@/features/employer/components/common'
import { CompanyCardView } from '@/features/employer/components/CompanyCardView'
import { Alert, Button, Input, Panel, Skeleton, TextArea, useToast } from '@/ui'

interface FormState {
  name: string
  field: string
  description: string
  email: string
  telegram: string
  phone: string
  inn: string
  website: string
  webhook_url: string
}

type Errors = Partial<Record<keyof FormState | 'contact', string>>

// Коды 400 бэкенда (backend/app/api/companies.py) → поле формы
const ERROR_FIELD: Record<string, keyof Errors> = {
  contact_email_invalid: 'email',
  contact_telegram_invalid: 'telegram',
  contact_phone_invalid: 'phone',
  inn_invalid: 'inn',
  website_invalid: 'website',
  webhook_url_invalid: 'webhook_url',
  description_too_long: 'description',
}

const DESCRIPTION_MAX = 3000 // config/contact.yaml: company_profile.description_max_len

function toForm(me: CompanyMe): FormState {
  return {
    name: me.name ?? '',
    field: me.field ?? '',
    description: me.description ?? '',
    email: me.contact_methods.email ?? '',
    telegram: me.contact_methods.telegram ?? '',
    phone: me.contact_methods.phone ?? '',
    inn: me.inn ?? '',
    website: me.website ?? '',
    webhook_url: me.webhook.url ?? '',
  }
}

function toBody(f: FormState): CompanyProfileIn {
  return {
    name: f.name.trim(),
    field: f.field.trim(),
    description: f.description.trim(),
    contact_methods: { email: f.email.trim(), telegram: f.telegram.trim(), phone: f.phone.trim() },
    inn: f.inn.trim(),
    website: f.website.trim(),
    webhook_url: f.webhook_url.trim(),
  }
}

/** Р1 «Профиль компании»: слева поля, справа «так видит кандидат» и надёжность. Без профиля приглашения не уходят. */
export default function CompanyProfile() {
  const company = useCompany()

  return (
    <div>
      <PageHead
        title="Профиль компании"
        lead="Кандидат видит эти данные до решения. Без названия, направления, описания и способа связи приглашения не отправляются."
      />
      <div className="mt-10">
        {company.isPending ? (
          <PageSkeleton rows={3} />
        ) : company.isError ? (
          <LoadError error={company.error} onRetry={() => void company.refetch()} />
        ) : (
          <ProfileForm me={company.data} />
        )}
      </div>
    </div>
  )
}

function ProfileForm({ me }: { me: CompanyMe }) {
  const toast = useToast()
  const save = useSaveCompany()
  const initial = useMemo(() => toForm(me), [me])
  const [form, setForm] = useState<FormState>(() => toForm(me))
  const [errors, setErrors] = useState<Errors>({})
  const [formError, setFormError] = useState<string | null>(null)

  const dirty = (Object.keys(form) as (keyof FormState)[]).some((k) => form[k] !== initial[k])
  const set = (k: keyof FormState) => (v: string) => {
    setForm((f) => ({ ...f, [k]: v }))
    setErrors((e) => ({ ...e, [k]: undefined, contact: undefined }))
  }

  const draftCard = useMemo(
    () => ({
      name: form.name.trim() || 'Компания без названия',
      field: form.field.trim() || null,
      description: form.description.trim() || null,
      website: form.website.trim() || null,
      personal_email_label: me.preview.personal_email_label,
      contact_methods: [
        form.email.trim() && { method: 'email', label: 'Email', value: form.email.trim() },
        form.telegram.trim() && { method: 'telegram', label: 'Telegram', value: form.telegram.trim() },
        form.phone.trim() && { method: 'phone', label: 'Телефон', value: form.phone.trim() },
      ].filter(Boolean) as { method: string; label: string; value: string }[],
      reliability: me.reliability,
    }),
    [form, me.preview.personal_email_label, me.reliability],
  )

  function validate(): Errors {
    const e: Errors = {}
    if (!form.name.trim()) e.name = 'Укажите название компании'
    if (!form.field.trim()) e.field = 'Укажите направление деятельности'
    if (form.description.trim().length < 30) e.description = 'Опишите компанию в двух-трёх предложениях: не короче 30 знаков'
    if (!form.email.trim() && !form.telegram.trim() && !form.phone.trim()) {
      e.contact = 'Укажите хотя бы один способ связи — он откроется кандидату только после его «да»'
    }
    return e
  }

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault()
    setFormError(null)
    const v = validate()
    setErrors(v)
    if (Object.keys(v).length > 0) return
    try {
      await save.mutateAsync(toBody(form))
      toast.success('Профиль компании сохранён')
    } catch (err) {
      if (err instanceof ApiException && ERROR_FIELD[err.code]) {
        setErrors({ [ERROR_FIELD[err.code]]: err.message })
      } else {
        setFormError(errorMessage(err))
      }
    }
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,440px)] lg:gap-14">
      <form onSubmit={onSubmit} noValidate className="flex min-w-0 flex-col gap-8">
        {!me.profile_complete && (
          <Alert tone="info" title="Профиль пока не готов к приглашениям">
            Не хватает: {me.missing.join(', ')}.
          </Alert>
        )}
        {me.profile_complete && (
          <Alert tone="success" title="Профиль заполнен">
            Приглашения можно отправлять. Изменения увидят кандидаты в новых приглашениях.
          </Alert>
        )}

        <section className="flex flex-col gap-5" aria-labelledby="sec-main">
          <BlockTitle>
            <span id="sec-main">О компании</span>
          </BlockTitle>
          <Input
            label="Название"
            value={form.name}
            maxLength={200}
            onChange={(e) => set('name')(e.target.value)}
            error={errors.name}
            placeholder="ООО «Северный код»"
            autoComplete="organization"
          />
          <Input
            label="Направление деятельности"
            value={form.field}
            maxLength={200}
            onChange={(e) => set('field')(e.target.value)}
            error={errors.field}
            placeholder="Финтех: платёжный шлюз"
          />
          <TextArea
            label="Описание"
            rows={5}
            value={form.description}
            maxLength={DESCRIPTION_MAX}
            onChange={(e) => set('description')(e.target.value)}
            error={errors.description}
            hint={`${form.description.length} из ${DESCRIPTION_MAX} знаков. Чем занимаетесь, размер команды, формат работы — как для коллеги.`}
          />
          <div className="grid gap-5 sm:grid-cols-2">
            <Input
              label="Сайт (необязательно)"
              value={form.website}
              onChange={(e) => set('website')(e.target.value)}
              error={errors.website}
              placeholder="https://northcode.example"
              inputMode="url"
            />
            <Input
              label="ИНН (необязательно)"
              value={form.inn}
              onChange={(e) => set('inn')(e.target.value)}
              error={errors.inn}
              hint="10 цифр для организации, 12 для ИП"
              inputMode="numeric"
              mono
            />
          </div>
        </section>

        <section className="flex flex-col gap-5" aria-labelledby="sec-contacts">
          <div>
            <BlockTitle>
              <span id="sec-contacts">Способы связи</span>
            </BlockTitle>
            <p className="mt-1 t-small text-ink-3">
              Кандидат видит их в приглашении, а вы получаете его контакты только после его «да». Нужен хотя бы один.
            </p>
          </div>
          {errors.contact && (
            <p role="alert" className="t-small text-danger-text">
              {errors.contact}
            </p>
          )}
          <div className="grid gap-5 sm:grid-cols-3">
            <Input
              label="Email"
              type="email"
              value={form.email}
              onChange={(e) => set('email')(e.target.value)}
              error={errors.email}
              placeholder="hr@northcode.example"
            />
            <Input
              label="Telegram"
              value={form.telegram}
              onChange={(e) => set('telegram')(e.target.value)}
              error={errors.telegram}
              placeholder="@northcode_hr"
            />
            <Input
              label="Телефон"
              type="tel"
              value={form.phone}
              onChange={(e) => set('phone')(e.target.value)}
              error={errors.phone}
              placeholder="+7 495 000-00-00"
            />
          </div>
        </section>

        <section className="flex flex-col gap-4" aria-labelledby="sec-ats">
          <div>
            <BlockTitle>
              <span id="sec-ats">Интеграция с ATS (необязательно)</span>
            </BlockTitle>
            <p className="mt-1 t-small text-ink-3">
              Адрес вебхука, на который приходят подписанные события: новое приглашение, ответ кандидата, открытые контакты.
            </p>
          </div>
          <Input
            label="URL вебхука"
            value={form.webhook_url}
            onChange={(e) => set('webhook_url')(e.target.value)}
            error={errors.webhook_url}
            placeholder="https://ats.northcode.example/hooks/vzaimno"
            inputMode="url"
          />
          {me.webhook.url && <WebhookTools me={me} dirty={form.webhook_url.trim() !== (me.webhook.url ?? '')} />}
        </section>

        {formError && <Alert tone="danger" title="Не удалось сохранить">{formError}</Alert>}

        <div className="flex flex-wrap items-center gap-4">
          <Button type="submit" size="lg" loading={save.isPending} disabled={!dirty && me.profile_complete}>
            Сохранить профиль
          </Button>
          {!dirty && me.profile_complete && <span className="t-small text-ink-3">Изменений нет</span>}
        </div>
      </form>

      <aside className="flex min-w-0 flex-col gap-6" aria-label="Как вас видят и надёжность">
        <CompanyCardView card={draftCard} />
        <Panel variant="panel" radius="lg" padding="md" className="flex flex-col gap-4">
          <BlockTitle>Квота и жалобы</BlockTitle>
          <dl className="m-0 grid grid-cols-2 gap-x-6 gap-y-3">
            <div>
              <Label>Квота на неделю</Label>
              <p className="t-label text-ink">{me.quota.label}</p>
            </div>
            <div>
              <Label>Жалобы за 90 дней</Label>
              <p className="t-label text-ink">{me.complaints_90d}</p>
            </div>
            {me.quota.reduced_reason && (
              <div className="col-span-2">
                <Alert tone="info" title="Квота снижена">
                  {me.quota.reduced_reason}
                </Alert>
              </div>
            )}
            {me.quota.next_slot_in && (
              <div className="col-span-2">
                <Label>Следующий слот освободится</Label>
                <p className="t-label text-ink">{me.quota.next_slot_in}</p>
              </div>
            )}
          </dl>
          <p className="t-small text-ink-3">
            Надёжность растёт, когда вы отвечаете в срок, связываетесь после «да» и условия в приглашении совпадают с
            реальными. Домен почты: <span className="font-mono">{me.email_domain}</span>
            {me.personal_domain ? ' (личная почта — квота считается на аккаунт)' : ''}.
          </p>
        </Panel>
      </aside>
    </div>
  )
}

/** Секрет подписи, проверка доставки и журнал последних событий. */
function WebhookTools({ me, dirty }: { me: CompanyMe; dirty: boolean }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [shown, setShown] = useState(false)
  const log = useQuery({
    queryKey: [...qk.company, 'webhooks'],
    queryFn: () => unwrap(api.GET('/api/companies/me/webhooks')),
  })
  const test = useMutation({
    mutationFn: () => unwrap(api.POST('/api/companies/me/webhooks/test')),
    onSuccess: (d) => {
      void log.refetch()
      if (d.status === 'delivered') toast.success('Тестовое событие доставлено')
      else toast.error(`Доставка не удалась (код ${d.response_code ?? '—'}). Повтор — по расписанию.`)
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  const rotate = useMutation({
    mutationFn: () => unwrap(api.POST('/api/companies/me/webhooks/rotate-secret')),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.company })
      toast.success('Новый секрет выдан, старый больше не действует')
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  return (
    <Panel variant="panel" radius="md" padding="sm" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Label>Секрет подписи (HMAC-SHA256)</Label>
        <code className="rounded-md bg-white px-2 py-1 font-mono text-[13px] text-ink [overflow-wrap:anywhere]">
          {me.webhook.secret ? (shown ? me.webhook.secret : '•'.repeat(24)) : 'выдаётся после сохранения'}
        </code>
        {me.webhook.secret && (
          <Button variant="text" onClick={() => setShown((v) => !v)}>
            {shown ? 'Скрыть' : 'Показать'}
          </Button>
        )}
      </div>
      <p className="t-small text-ink-3">
        Заголовки: <span className="font-mono">{me.webhook.signature_header}</span>,{' '}
        <span className="font-mono">{me.webhook.event_header}</span>, <span className="font-mono">{me.webhook.event_id_header}</span>
      </p>
      <div className="flex flex-wrap gap-3">
        <Button
          size="sm"
          variant="outline"
          loading={test.isPending}
          disabled={dirty}
          title={dirty ? 'Сначала сохраните профиль' : undefined}
          onClick={() => test.mutate()}
        >
          Проверить доставку
        </Button>
        <Button size="sm" variant="outline" loading={rotate.isPending} disabled={dirty} onClick={() => rotate.mutate()}>
          Выдать новый секрет
        </Button>
      </div>
      <div>
        <Label className="mb-1">Последние доставки</Label>
        {log.isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : log.isError ? (
          <p className="t-small text-danger-text">{errorMessage(log.error)}</p>
        ) : log.data.deliveries.length === 0 ? (
          <p className="t-small text-ink-3">Доставок пока не было.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
            {log.data.deliveries.map((d) => (
              <li key={d.event_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 t-small">
                <span className="font-mono text-ink">{d.event_type}</span>
                <Tag tone={d.status === 'delivered' ? 'brand' : d.status === 'failed' ? 'danger' : 'default'}>
                  {d.status === 'delivered' ? 'доставлено' : d.status === 'failed' ? 'не доставлено' : 'в очереди'}
                </Tag>
                <span className="text-ink-3">
                  {d.response_code ? `код ${d.response_code} · ` : ''}попыток: {d.attempts}
                  {d.last_attempt_text ? ` · ${d.last_attempt_text}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  )
}
