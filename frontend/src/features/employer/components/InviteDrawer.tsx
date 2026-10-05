import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { formatInt } from '@/lib/format'
import { Alert, Button, Checkbox, Input, Panel, Segmented, Skeleton, TextArea, useToast } from '@/ui'
import { Drawer } from '@/ui/Drawer'
import { Select } from '@/ui/Select'
import { useCompany, useInvalidateInvitations, type InvitationCreate, type Need, type SendResult } from '../api'
import { salaryState } from '../brief'
import { FORMAT_LABEL, RESPONSE_DAYS } from '../dict'
import { useDebounced } from '../hooks'
import { CompanyCardView } from './CompanyCardView'
import { SalaryFields } from './SalaryRange'
import { BlockTitle, Label, Tag } from './common'

export interface InviteCandidate {
  ref: string
  pseudonym: string
  /** «Почему вы» по умолчанию — те же «за» из подборки, переписанные на «вы» */
  whyYou: string[]
  /** Сигнал по зарплате: «в вилке ✓» / «выше вилки» */
  salarySignal?: string | null
}

const DESCRIPTION_MIN = 50 // config/contact.yaml: invitation.description_min_len
const DESCRIPTION_MAX = 3000
const TITLE_MAX = 120
const WHY_MAX_ITEMS = 3
const BATCH_MAX = 10

/**
 * Р7 «Приглашение»: форма заполнена из брифа и профиля компании, для каждого кандидата — свой текст «почему вы»,
 * справа в потоке — превью «так увидит кандидат» и проверки квоты, лимитов и паузы словами.
 * Пригласить можно одного кандидата или пачку из шортлиста (≤ 10); контакты откроются только после его «да».
 */
export function InviteDrawer({
  open,
  onClose,
  need,
  candidates,
}: {
  open: boolean
  onClose: () => void
  need: Need | null
  candidates: InviteCandidate[]
}) {
  if (!open) return null
  return <InviteForm need={need} candidates={candidates} onClose={onClose} />
}

function InviteForm({ need, candidates, onClose }: { need: Need | null; candidates: InviteCandidate[]; onClose: () => void }) {
  const toast = useToast()
  const company = useCompany()
  const invalidate = useInvalidateInvitations()
  const me = company.data

  const methods = useMemo(() => Object.entries(me?.contact_methods ?? {}).filter(([, v]) => !!v), [me])
  const [title, setTitle] = useState(need?.title || need?.category || '')
  const [description, setDescription] = useState(need?.text ?? '')
  const [from, setFrom] = useState<number | null>(need?.salary_from ?? null)
  const [to, setTo] = useState<number | null>(need?.salary_to ?? null)
  const [net, setNet] = useState(need?.salary_net ?? true)
  const [format, setFormat] = useState<string>(need?.brief.formats[0] ?? '')
  const [startDate, setStartDate] = useState('')
  const [method, setMethod] = useState<string>('')
  const [days, setDays] = useState<number>(5)
  const [included, setIncluded] = useState<Set<string>>(() => new Set(candidates.slice(0, BATCH_MAX).map((c) => c.ref)))
  const [why, setWhy] = useState<Record<string, string>>(() =>
    Object.fromEntries(candidates.map((c) => [c.ref, c.whyYou.join('\n')])),
  )
  const [result, setResult] = useState<SendResult | null>(null)
  const [touched, setTouched] = useState(false)

  const contactMethod = method || methods[0]?.[0] || ''
  const contactValue = (me?.contact_methods ?? {})[contactMethod] ?? ''
  const refs = candidates.filter((c) => included.has(c.ref)).map((c) => c.ref)
  const whyFor = (ref: string) =>
    (why[ref] ?? '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, WHY_MAX_ITEMS)

  const sal = salaryState(from, to)
  const salaryError =
    sal.state === 'blocked'
      ? 'Вилка шире чем в 2 раза. Сузьте её: кандидат должен видеть реальные условия.'
      : sal.state === 'invalid'
        ? 'Проверьте вилку: «от» — не меньше 30 000 ₽, «до» — не меньше «от».'
        : sal.state === 'empty' && touched
          ? 'Вилка обязательна — это условие приглашения.'
          : null
  const salaryWarning =
    sal.state === 'warn'
      ? 'Вилка шире чем в 1,6 раза. Отправить можно, но кандидат увидит предупреждение о широкой вилке.'
      : null
  const descError =
    touched && description.trim().length < DESCRIPTION_MIN ? `Описание — не короче ${DESCRIPTION_MIN} знаков: что за позиция и чем заниматься.` : null
  const profileMissing = me && !me.profile_complete

  const formBody = useMemo(
    () => ({
      need_id: need?.id ?? null,
      title: title.trim() || null,
      description: description.trim() || null,
      salary_from: from,
      salary_to: to,
      salary_type: (net ? 'net' : 'gross') as 'net' | 'gross',
      format: format || null,
      start_date: startDate || null,
      contact_method: contactMethod || null,
      contact_value: contactValue || null,
      response_days: days,
    }),
    [need?.id, title, description, from, to, net, format, startDate, contactMethod, contactValue, days],
  )
  const previewBody = useDebounced(
    {
      ...formBody,
      candidates: refs,
      why_you: Object.fromEntries(refs.map((r) => [r, whyFor(r)])),
    },
    450,
  )

  const preview = useQuery({
    queryKey: ['employer', 'invite-preview', JSON.stringify(previewBody)],
    enabled: refs.length > 0 && !!me?.profile_complete && sal.state !== 'empty' && sal.state !== 'invalid',
    placeholderData: keepPreviousData,
    retry: false,
    queryFn: () => unwrap(api.POST('/api/invitations/preview', { body: previewBody })),
  })

  const send = useMutation({
    mutationFn: async (): Promise<SendResult> => {
      const why_you = Object.fromEntries(refs.map((r) => [r, whyFor(r)]))
      if (refs.length === 1) {
        const body: InvitationCreate = { ...formBody, candidate: refs[0], why_you: why_you[refs[0]] }
        return unwrap(api.POST('/api/invitations', { body }))
      }
      return unwrap(api.POST('/api/invitations/batch', { body: { ...formBody, candidates: refs, why_you } }))
    },
    onSuccess: (r) => {
      invalidate()
      if (r.rejected.length === 0) {
        toast.success(r.sent.length === 1 ? 'Приглашение отправлено' : `Отправлено приглашений: ${r.sent.length}`)
        onClose()
      } else {
        setResult(r)
      }
    },
  })

  const checks = new Map((preview.data?.checks ?? []).map((c) => [c.ref, c]))
  const okRefs = refs.filter((r) => checks.get(r)?.ok !== false)
  const canSend =
    !profileMissing &&
    refs.length > 0 &&
    refs.length <= BATCH_MAX &&
    sal.state !== 'blocked' &&
    sal.state !== 'invalid' &&
    sal.state !== 'empty' &&
    description.trim().length >= DESCRIPTION_MIN &&
    title.trim().length > 0 &&
    !!contactMethod &&
    (preview.data ? okRefs.length > 0 : true)

  const single = candidates.length === 1
  const shell = (children: ReactNode, footer?: ReactNode) => (
    <Drawer
      open
      onClose={onClose}
      width={680}
      eyebrow={need ? `Потребность «${need.title || need.category}»` : 'Приглашение из банка'}
      title={single ? `Пригласить ${candidates[0].pseudonym}` : 'Пригласить выбранных'}
      footer={footer}
    >
      {children}
    </Drawer>
  )

  if (result) {
    return shell(
      <div className="flex flex-col gap-5">
        <Alert tone={result.sent.length > 0 ? 'success' : 'danger'} title={result.sent.length > 0 ? `Отправлено: ${result.sent.length}` : 'Ничего не отправлено'}>
          {result.sent.map((s) => s.pseudonym).join(', ')}
        </Alert>
        {result.rejected.length > 0 && (
          <div className="flex flex-col gap-3">
            <BlockTitle>Не отправлено</BlockTitle>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {result.rejected.map((r) => (
                <li key={r.ref}>
                  <p className="t-label text-ink">{r.pseudonym}</p>
                  {r.errors.map((e) => (
                    <p key={e.code} className="t-small text-danger-text">
                      {e.message}
                    </p>
                  ))}
                </li>
              ))}
            </ul>
          </div>
        )}
        {[...result.warnings, ...result.notes].map((w) => (
          <p key={w} className="t-small text-ink-2">
            {w}
          </p>
        ))}
        <p className="t-small text-ink-3">{result.quota.label} приглашений на неделе.</p>
        <div className="flex gap-3">
          <Button to="/employer/invitations">К приглашениям</Button>
          <Button variant="outline" onClick={onClose}>
            Закрыть
          </Button>
        </div>
      </div>,
    )
  }

  return shell(
    <form
      id="invite-form"
      noValidate
      className="flex flex-col gap-8"
      onSubmit={(e) => {
        e.preventDefault()
        setTouched(true)
        if (canSend) send.mutate()
      }}
    >
      {profileMissing && (
        <Alert tone="danger" title="Профиль компании не заполнен">
          Без него приглашения не отправляются. Не хватает: {me.missing.join(', ')}.{' '}
          <Link to="/employer/company" className="text-brand underline underline-offset-4">
            Заполнить профиль
          </Link>
        </Alert>
      )}

      <section className="flex flex-col gap-3" aria-labelledby="inv-cands">
        <BlockTitle>
          <span id="inv-cands">
            Кому: {refs.length} из {candidates.length}
          </span>
        </BlockTitle>
        {candidates.length > BATCH_MAX && (
          <p className="t-small text-ink-3">За раз можно пригласить не больше {BATCH_MAX}: отправим первых {BATCH_MAX} из шортлиста.</p>
        )}
        <ul className="m-0 flex list-none flex-col gap-4 p-0">
          {candidates.map((c) => {
            const on = included.has(c.ref)
            const chk = checks.get(c.ref)
            return (
              <li key={c.ref} className="flex flex-col gap-2 rounded-panel border border-hairline p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  {candidates.length > 1 ? (
                    <Checkbox
                      checked={on}
                      disabled={!on && included.size >= BATCH_MAX}
                      onChange={(e) =>
                        setIncluded((cur) => {
                          const next = new Set(cur)
                          if (e.target.checked) next.add(c.ref)
                          else next.delete(c.ref)
                          return next
                        })
                      }
                      label={<span className="t-name">{c.pseudonym}</span>}
                      wrapperClassName="min-w-0"
                    />
                  ) : (
                    <span className="t-name text-ink">{c.pseudonym}</span>
                  )}
                  {c.salarySignal && <Tag>{c.salarySignal}</Tag>}
                </div>
                {on && (
                  <>
                    <TextArea
                      label="Почему вы — кандидат прочтёт это в приглашении"
                      rows={3}
                      value={why[c.ref] ?? ''}
                      onChange={(e) => setWhy((w) => ({ ...w, [c.ref]: e.target.value }))}
                      hint={`Один пункт — одна строка, до ${WHY_MAX_ITEMS}. Пишите на «вы». Контакты и ссылки в тексте до «да» запрещены.`}
                    />
                    {chk && <CheckLines check={chk} />}
                  </>
                )}
              </li>
            )
          })}
        </ul>
      </section>

      <section className="flex flex-col gap-5" aria-labelledby="inv-form">
        <BlockTitle>
          <span id="inv-form">Условия приглашения</span>
        </BlockTitle>
        <Input
          label="Заголовок"
          value={title}
          maxLength={TITLE_MAX}
          onChange={(e) => setTitle(e.target.value)}
          error={touched && !title.trim() ? 'Укажите заголовок позиции' : undefined}
        />
        <TextArea
          label="Описание позиции"
          rows={4}
          value={description}
          maxLength={DESCRIPTION_MAX}
          onChange={(e) => setDescription(e.target.value)}
          error={descError}
          hint={need ? 'Подставили текст потребности — отредактируйте под приглашение.' : `Не короче ${DESCRIPTION_MIN} знаков.`}
        />
        <SalaryFields from={from} to={to} net={net} onFrom={setFrom} onTo={setTo} onNet={setNet} error={salaryError} warning={salaryWarning} />
        <div className="grid gap-5 sm:grid-cols-2">
          <Select label="Формат работы" value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="">Не указывать</option>
            {Object.entries(FORMAT_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
          <Input label="Желаемая дата выхода" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </div>
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Как связаться после «да»"
            value={contactMethod}
            onChange={(e) => setMethod(e.target.value)}
            disabled={methods.length === 0}
            hint={contactValue ? `Кандидат получит: ${contactValue}` : 'Добавьте способ связи в профиле компании'}
          >
            {methods.length === 0 && <option value="">Не указан</option>}
            {methods.map(([k]) => (
              <option key={k} value={k}>
                {k === 'email' ? 'Email' : k === 'telegram' ? 'Telegram' : 'Телефон'}
              </option>
            ))}
          </Select>
          <div className="flex flex-col gap-2">
            <Label>Срок ответа кандидата</Label>
            <Segmented
              aria-label="Срок ответа кандидата, дней"
              options={RESPONSE_DAYS.map((d) => ({ value: String(d), label: `${d} дн.` }))}
              value={String(days)}
              onChange={(v) => setDays(Number(v))}
            />
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-4" aria-labelledby="inv-preview">
        <BlockTitle>
          <span id="inv-preview">Так увидит кандидат</span>
        </BlockTitle>
        {!preview.data && preview.isFetching ? (
          <Skeleton className="h-40 w-full" />
        ) : preview.isError ? (
          <Alert tone="danger" title="Не удалось собрать превью">
            {errorMessage(preview.error)}
          </Alert>
        ) : preview.data ? (
          <div className="flex flex-col gap-4">
            {preview.data.caution && (
              <Alert tone="danger" title={preview.data.caution}>
                {preview.data.caution_reasons.join('; ')}
              </Alert>
            )}
            <Panel variant="outline" radius="xl" padding="md" className="flex flex-col gap-3">
              <p className="t-num-l text-ink">{preview.data.salary.text}</p>
              <p className="t-name text-ink [overflow-wrap:anywhere]">{preview.data.title}</p>
              <p className="whitespace-pre-line t-body text-ink-2 [overflow-wrap:anywhere]">{preview.data.description}</p>
              <p className="t-small text-ink-3">
                {[preview.data.format_label, preview.data.contact_method_label && `связь: ${preview.data.contact_method_label}`, `ответить в течение ${preview.data.respond_in_days} дн.`]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              <div className="flex gap-2">
                <span className="rounded-pill bg-brand px-4 py-2 t-label text-white">Да</span>
                <span className="rounded-pill border-[1.5px] border-brand px-4 py-2 t-label text-brand">Да, если…</span>
                <span className="rounded-pill border border-steel px-4 py-2 t-label text-ink-2">Нет</span>
              </div>
            </Panel>
            <CompanyCardView
              title="Компания в приглашении"
              card={{ ...preview.data.company, contact_methods: preview.data.company.contact_methods }}
            />
          </div>
        ) : (
          <p className="t-body text-ink-3">Заполните вилку и описание — здесь появится приглашение глазами кандидата.</p>
        )}
      </section>

      <section aria-label="Квота и лимиты" className="flex flex-col gap-1 rounded-panel bg-panel p-4">
        <p className="t-label text-ink">
          Квота: {preview.data?.quota.label ?? me?.quota.label ?? '—'} на неделю
          {(preview.data?.quota.next_slot_in ?? me?.quota.next_slot_in) ? `, следующий слот освободится ${preview.data?.quota.next_slot_in ?? me?.quota.next_slot_in}` : ''}
        </p>
        {(preview.data?.quota.reduced_reason ?? me?.quota.reduced_reason) && (
          <p className="t-small text-ink-2">{preview.data?.quota.reduced_reason ?? me?.quota.reduced_reason}</p>
        )}
        <p className="t-small text-ink-3">
          Правила: до {BATCH_MAX} кандидатов за раз, не больше 20 ждущих ответа на потребность и 60 новых за неделю на домен почты.
          У каждого кандидата свой недельный лимит приглашений и паузы после отказа.
        </p>
      </section>

      {send.isError && (
        <Alert tone="danger" title="Приглашение не отправлено">
          {errorMessage(send.error)}
        </Alert>
      )}

    </form>,
    <div className="flex flex-wrap items-center gap-4">
      <Button type="submit" form="invite-form" size="lg" loading={send.isPending} disabled={!!profileMissing || sal.state === 'blocked' || sal.state === 'invalid'}>
        {refs.length > 1 ? `Отправить (${formatInt(preview.data ? okRefs.length : refs.length)})` : 'Отправить приглашение'}
      </Button>
      <Button variant="outline" onClick={onClose}>
        Отмена
      </Button>
      {preview.data && refs.length > 1 && okRefs.length < refs.length && (
        <span className="t-small text-ink-3">
          Уйдёт {okRefs.length} из {refs.length}: остальным мешают проверки выше.
        </span>
      )}
    </div>,
  )
}

function CheckLines({ check }: { check: Schemas['PreviewCheckOut'] }) {
  if (check.errors.length === 0 && check.warnings.length === 0 && check.notes.length === 0) {
    return <p className="t-small text-brand">Проверки пройдены — можно отправлять.</p>
  }
  return (
    <ul className="m-0 flex list-none flex-col gap-1 p-0">
      {check.errors.map((e) => (
        <li key={e.code} className="t-small text-danger-text">
          {e.message}
        </li>
      ))}
      {check.warnings.map((w) => (
        <li key={w.code} className="t-small text-ink-2">
          {w.message}
        </li>
      ))}
      {check.notes.map((n) => (
        <li key={n.code} className="t-small text-ink-3">
          {n.message}
        </li>
      ))}
    </ul>
  )
}
