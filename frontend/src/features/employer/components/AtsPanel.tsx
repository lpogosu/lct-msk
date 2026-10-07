import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { Alert, Button, Checkbox, Input, PasswordInput, Panel, Segmented, Skeleton, useToast } from '@/ui'
import { BlockTitle, Label, Tag } from './common'

type AtsSettings = Schemas['AtsSettingsOut']
type AtsLogItem = Schemas['AtsLogItem']
type ApiKey = Schemas['ApiKeyOut']

const KEY = ['employer', 'ats'] as const

const MODE_TEXT: Record<string, string> = {
  none: 'не подключена',
  emulator: 'эмулятор: запросы только записываются в журнал',
  real: 'боевой режим: запросы уходят в Хантфлоу',
}

/**
 * «Интеграция с ATS»: тип (Нет / Вебхук / Хантфлоу), токен, проверка подключения, API-ключи для pull-API
 * и «Журнал ATS». Контакты кандидатов уходят в ATS только после взаимного «да».
 */
export function AtsPanel() {
  const settings = useQuery({ queryKey: [...KEY, 'settings'], queryFn: () => unwrap(api.GET('/api/companies/me/ats')) })

  return (
    <section className="flex flex-col gap-5" aria-labelledby="sec-ats-integration">
      <div>
        <BlockTitle>
          <span id="sec-ats-integration">Система найма (ATS)</span>
        </BlockTitle>
        <p className="mt-1 max-w-[760px] t-small text-ink-3">
          Платформа встраивается в ваш процесс: вакансии, отклики и статусы синхронизируются с внутренней системой найма. Контакты кандидата
          попадают в ATS только после его «да» и только те поля, что он разрешил.
        </p>
      </div>
      {settings.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : settings.isError ? (
        <Alert tone="danger" title="Не удалось загрузить настройки">
          {errorMessage(settings.error)}
        </Alert>
      ) : (
        <>
          <TypeForm s={settings.data} />
          <ApiKeys scopes={settings.data.scopes} />
          <AtsLog />
        </>
      )}
    </section>
  )
}

function TypeForm({ s }: { s: AtsSettings }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [type, setType] = useState(s.ats_type)
  const [baseUrl, setBaseUrl] = useState(s.base_url ?? '')
  const [account, setAccount] = useState(s.account_id ?? '')
  const [token, setToken] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [checkMsg, setCheckMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT('/api/companies/me/ats', {
          body: {
            ats_type: type,
            base_url: baseUrl.trim(),
            account_id: account.trim(),
            ...(token.trim() ? { token: token.trim() } : {}),
          },
        }),
      ),
    onSuccess: (data) => {
      qc.setQueryData([...KEY, 'settings'], data)
      setToken('')
      setFormError(null)
      toast.success('Настройки ATS сохранены')
    },
    onError: (e) => setFormError(errorMessage(e)),
  })
  const clearToken = useMutation({
    mutationFn: () => unwrap(api.PUT('/api/companies/me/ats', { body: { ats_type: type, token: '' } })),
    onSuccess: (data) => {
      qc.setQueryData([...KEY, 'settings'], data)
      toast.success('Токен удалён, включён эмулятор')
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  const check = useMutation({
    mutationFn: () => unwrap(api.POST('/api/companies/me/ats/check')),
    onSuccess: (d) => {
      setCheckMsg({ ok: d.ok, text: d.message })
      void qc.invalidateQueries({ queryKey: [...KEY, 'log'] })
    },
    onError: (e) => setCheckMsg({ ok: false, text: errorMessage(e) }),
  })

  const options = Object.entries(s.ats_types).map(([value, label]) => ({ value, label }))
  const saved = s.ats_type === 'huntflow'

  return (
    <Panel variant="panel" radius="md" padding="md" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Label>Тип интеграции</Label>
        <Segmented options={options} value={type} onChange={setType} aria-label="Тип ATS" />
      </div>

      {type === 'webhook' && (
        <p className="t-small text-ink-3">
          Подписанные события уходят на URL вебхука из блока выше (HMAC-SHA256). Для забора данных по запросу выдайте API-ключ ниже.
        </p>
      )}

      {type === 'huntflow' && (
        <div className="flex flex-col gap-4">
          <p className="t-small text-ink-3">
            Режим: <span className="text-ink">{MODE_TEXT[s.mode]}</span>. Без токена работает эмулятор: ровно те запросы, что ушли бы в
            Хантфлоу, записываются в журнал ниже. Укажите токен — запросы начнут отправляться.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Адрес API (необязательно)"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={s.base_url_default}
              inputMode="url"
            />
            <Input label="Номер организации" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="например, 42" inputMode="numeric" mono />
          </div>
          <PasswordInput
            label="Токен доступа Хантфлоу"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
            hint={
              s.token_set
                ? `Токен сохранён (${s.token_hint}), хранится зашифрованным. Введите новый, чтобы заменить.`
                : 'Хранится зашифрованным и обратно не показывается.'
            }
          />
        </div>
      )}

      {formError && (
        <Alert tone="danger" title="Не удалось сохранить">
          {formError}
        </Alert>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>
          Сохранить
        </Button>
        {saved && (
          <Button size="sm" variant="outline" loading={check.isPending} onClick={() => check.mutate()} disabled={type !== s.ats_type}>
            Проверить подключение
          </Button>
        )}
        {saved && s.token_set && (
          <Button size="sm" variant="text" loading={clearToken.isPending} onClick={() => clearToken.mutate()}>
            Удалить токен
          </Button>
        )}
      </div>
      {checkMsg && (
        <Alert tone={checkMsg.ok ? 'success' : 'danger'} title={checkMsg.ok ? 'Подключение проверено' : 'Проверка не прошла'}>
          {checkMsg.text}
        </Alert>
      )}
    </Panel>
  )
}

function ApiKeys({ scopes }: { scopes: Record<string, string> }) {
  const toast = useToast()
  const qc = useQueryClient()
  const keys = useQuery({ queryKey: [...KEY, 'keys'], queryFn: () => unwrap(api.GET('/api/companies/me/ats/keys')) })
  const [name, setName] = useState('')
  const [picked, setPicked] = useState<string[]>(['vacancies:read', 'invitations:read', 'applications:read'])
  const [issued, setIssued] = useState<{ name: string; key: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = () => void qc.invalidateQueries({ queryKey: [...KEY, 'keys'] })

  const create = useMutation({
    mutationFn: () => unwrap(api.POST('/api/companies/me/ats/keys', { body: { name: name.trim(), scopes: picked } })),
    onSuccess: (d) => {
      setIssued({ name: d.name, key: d.key })
      setName('')
      setError(null)
      refresh()
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const rotate = useMutation({
    mutationFn: (id: string) => unwrap(api.POST('/api/companies/me/ats/keys/{key_pk}/rotate', { params: { path: { key_pk: id } } })),
    onSuccess: (d) => {
      setIssued({ name: d.name, key: d.key })
      refresh()
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  const revoke = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE('/api/companies/me/ats/keys/{key_pk}', { params: { path: { key_pk: id } } })),
    onSuccess: () => {
      refresh()
      toast.success('Ключ отозван')
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  const toggle = (s: string) => setPicked((p) => (p.includes(s) ? p.filter((x) => x !== s) : [...p, s]))

  return (
    <Panel variant="panel" radius="md" padding="md" className="flex flex-col gap-4">
      <div>
        <Label>API-ключи для вашей ATS</Label>
        <p className="mt-1 t-small text-ink-3">
          Адрес: <span className="font-mono">/api/ats/v1</span>, заголовок <span className="font-mono">Authorization: Bearer vzk_…</span>. Вакансии,
          приглашения и отклики с курсором <span className="font-mono">updated_since</span>; статусы обратно — POST .../status. Описание — в
          документации ATS-интеграции.
        </p>
      </div>

      {issued && (
        <Alert tone="success" title={`Ключ «${issued.name}» — сохраните сейчас, потом он не покажется`}>
          <code className="mt-1 block break-all rounded-md bg-white px-2 py-1 font-mono text-[13px] text-ink">{issued.key}</code>
          <div className="mt-2 flex gap-3">
            <Button
              size="sm"
              variant="outline"
              onClick={() => void navigator.clipboard?.writeText(issued.key).then(() => toast.success('Скопировано'))}
            >
              Копировать
            </Button>
            <Button size="sm" variant="text" onClick={() => setIssued(null)}>
              Готово
            </Button>
          </div>
        </Alert>
      )}

      {keys.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : keys.isError ? (
        <p className="t-small text-danger-text">{errorMessage(keys.error)}</p>
      ) : keys.data.length === 0 ? (
        <p className="t-small text-ink-3">Ключей пока нет.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
          {keys.data.map((k: ApiKey) => (
            <li key={k.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2">
              <span className="t-label text-ink">{k.name}</span>
              <span className="font-mono t-small text-ink-3">vzk_{k.key_id}_…</span>
              {k.scopes.map((s) => (
                <Tag key={s}>{s}</Tag>
              ))}
              <span className="t-small text-ink-3">{k.last_used_at ? 'использовался' : 'не использовался'}</span>
              <span className="ml-auto flex gap-2">
                <Button size="sm" variant="outline" loading={rotate.isPending && rotate.variables === k.id} onClick={() => rotate.mutate(k.id)}>
                  Перевыпустить
                </Button>
                <Button size="sm" variant="text" loading={revoke.isPending && revoke.variables === k.id} onClick={() => revoke.mutate(k.id)}>
                  Отозвать
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-col gap-3 border-t border-hairline pt-4">
        <Input label="Название нового ключа" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Хантфлоу, прод" />
        <div className="grid gap-x-6 sm:grid-cols-2">
          {Object.entries(scopes).map(([code, label]) => (
            <Checkbox
              key={code}
              checked={picked.includes(code)}
              onChange={() => toggle(code)}
              label={<span className="font-mono text-[13px]">{code}</span>}
              description={label}
            />
          ))}
        </div>
        {error && (
          <p role="alert" className="t-small text-danger-text">
            {error}
          </p>
        )}
        <div>
          <Button size="sm" loading={create.isPending} disabled={!name.trim() || picked.length === 0} onClick={() => create.mutate()}>
            Создать ключ
          </Button>
        </div>
      </div>
    </Panel>
  )
}

function AtsLog() {
  const log = useQuery({ queryKey: [...KEY, 'log'], queryFn: () => unwrap(api.GET('/api/companies/me/ats/log')) })
  const [open, setOpen] = useState<number | null>(null)

  return (
    <Panel variant="panel" radius="md" padding="md" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <Label>Журнал ATS</Label>
        <Button size="sm" variant="text" onClick={() => void log.refetch()}>
          Обновить
        </Button>
      </div>
      {log.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : log.isError ? (
        <p className="t-small text-danger-text">{errorMessage(log.error)}</p>
      ) : log.data.items.length === 0 ? (
        <p className="t-small text-ink-3">
          Записей пока нет: они появятся после «Проверить подключение», раскрытия контактов или статуса из вашей ATS.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-hairline p-0">
          {log.data.items.map((i: AtsLogItem) => (
            <li key={i.id} className="py-2">
              <button
                type="button"
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left"
                onClick={() => setOpen(open === i.id ? null : i.id)}
                aria-expanded={open === i.id}
              >
                <Tag tone={i.status === 'failed' || i.status === 'rejected' ? 'danger' : i.status === 'recorded' ? 'default' : 'brand'}>
                  {i.direction === 'out' ? (i.mode === 'emulator' ? 'эмулятор' : 'исходящий') : 'входящий'}
                </Tag>
                <span className="font-mono t-small text-ink">{i.kind}</span>
                <span className="t-small text-ink-3">
                  {i.status}
                  {i.response_code ? ` · ${i.response_code}` : ''}
                  {i.when ? ` · ${i.when}` : ''}
                </span>
              </button>
              {open === i.id && (
                <pre className="mt-2 max-h-[280px] overflow-auto rounded-md bg-white p-3 font-mono text-[12px] leading-[18px] text-ink">
                  {`${i.method ?? ''} ${i.url ?? ''}\n${JSON.stringify(i.request, null, 2)}\n→ ${JSON.stringify(i.response, null, 2)}`}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
