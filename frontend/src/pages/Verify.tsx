import { useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, unwrap } from '@/api/client'
import type { components } from '@/api/schema'
import { API_BASE } from '@/api/session'
import { Alert, Button, Panel, Skeleton, TrustNode } from '@/ui'

type CredentialVerify = components['schemas']['VerifyCredentialOut']

/** Коды паспорта навыков начинаются с «vc» (PDF-коды — чистый hex), поэтому маршрут /v/:code общий. */
const isPassportCode = (code: string) => code.startsWith('vc')

const fmt = (d?: string | null) => (d ? new Date(d).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : '—')

/**
 * Публичная проверка без входа и без ПДн: PDF-профиль по коду из QR (концепция §3.1.2) и «Цифровой паспорт навыков»
 * (подписанные учётные данные, docs/CREDENTIALS.md) — по коду либо по загруженному файлу.
 */
export default function VerifyProfile() {
  const { code = '' } = useParams()
  const passport = isPassportCode(code)
  return (
    <div className="mx-auto flex max-w-[640px] flex-col gap-6 px-4 py-8 sm:px-6">
      <header>
        <p className="t-small text-ink-3">{passport ? 'Проверка паспорта навыков' : code ? 'Сверка PDF-профиля' : 'Проверка документа'}</p>
        <h1 className="t-title text-brand">
          {code && !passport ? 'Выдан ли этот документ платформой?' : 'Подлинный ли это паспорт навыков?'}
        </h1>
      </header>
      {passport && <PassportByCode code={code} />}
      {code && !passport && <PdfByCode code={code} />}
      <FileCheck />
      <Button variant="text" to="/">
        На главную
      </Button>
    </div>
  )
}

function PdfByCode({ code }: { code: string }) {
  const q = useQuery({
    queryKey: ['verify', code],
    queryFn: () => unwrap(api.GET('/api/v/{code}', { params: { path: { code } } })),
    retry: false,
  })
  const v = q.data
  return (
    <>
      {q.isLoading && <Skeleton className="h-40 w-full" />}
      {q.isError && <Alert tone="danger">Не удалось выполнить сверку. Проверьте ссылку и попробуйте ещё раз.</Alert>}
      {v && !v.valid && (
        <Panel padding="lg" className="flex flex-col gap-3">
          <h2 className="t-body-strong text-ink">Документ не найден</h2>
          <p className="t-body text-ink-2">{v.message} Платформа такого PDF не выдавала либо он был изменён.</p>
        </Panel>
      )}
      {v && v.valid && (
        <Panel padding="lg" className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <TrustNode kind="test" />
            <h2 className="t-body-strong text-ink">{v.message}</h2>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 t-body">
            <dt className="text-ink-3">Категория</dt>
            <dd className="text-ink">{v.category?.name ?? '—'}</dd>
            <dt className="text-ink-3">Версия</dt>
            <dd className="text-ink">{v.variant_label ?? '—'}</dd>
            <dt className="text-ink-3">Подтверждено тестом</dt>
            <dd className="text-ink">{fmt(v.confirmed_on)}</dd>
            <dt className="text-ink-3">Выдан</dt>
            <dd className="text-ink">{fmt(v.issued_on)}</dd>
            {v.interval && (
              <>
                <dt className="text-ink-3">Интервал на линейке</dt>
                <dd className="text-ink">
                  {v.interval[0]}–{v.interval[1]}
                </dd>
              </>
            )}
          </dl>
          {v.actual_note && <p className="t-small text-ink-2">{v.actual_note}</p>}
        </Panel>
      )}
    </>
  )
}

function PassportByCode({ code }: { code: string }) {
  const q = useQuery({
    queryKey: ['verify-passport', code],
    queryFn: async (): Promise<CredentialVerify | null> => {
      const res = await fetch(`${API_BASE}/api/credentials/verify/${encodeURIComponent(code)}`)
      if (res.status === 404) return null
      if (!res.ok) throw new Error(String(res.status))
      return (await res.json()) as CredentialVerify
    },
    retry: false,
  })
  if (q.isLoading) return <Skeleton className="h-40 w-full" />
  if (q.isError) return <Alert tone="danger">Не удалось выполнить проверку. Проверьте ссылку и попробуйте ещё раз.</Alert>
  if (q.data === null) return <Verdict ok={false} title="Паспорт не найден" reasons={['Паспорт с таким кодом платформа не выдавала.']} />
  return q.data ? <Result r={q.data} /> : null
}

function FileCheck() {
  const input = useRef<HTMLInputElement>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const check = useMutation({
    mutationFn: async (file: File): Promise<CredentialVerify> => {
      const text = await file.text()
      const res = await fetch(`${API_BASE}/api/credentials/verify`, { method: 'POST', body: text, headers: { 'Content-Type': 'application/json' } })
      if (!res.ok) throw new Error(String(res.status))
      return (await res.json()) as CredentialVerify
    },
  })
  return (
    <Panel variant="panel" radius="lg" padding="lg" className="flex flex-col gap-3">
      <h2 className="t-body-strong text-ink">Проверить файл</h2>
      <p className="t-small text-ink-2">
        Загрузите JSON-файл паспорта навыков. Файл остаётся у вас: проверяется подпись издателя, срок действия и отзыв. Изменённый хотя бы на один байт файл
        не пройдёт.
      </p>
      <input
        ref={input}
        type="file"
        accept="application/json,.json"
        className="sr-only"
        aria-label="Файл паспорта навыков (JSON)"
        data-testid="passport-file"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) {
            setFileName(f.name)
            check.mutate(f)
          }
          e.target.value = ''
        }}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" loading={check.isPending} onClick={() => input.current?.click()}>
          Выбрать файл
        </Button>
        {fileName && <span className="t-small text-ink-3 [overflow-wrap:anywhere]">{fileName}</span>}
      </div>
      {check.isError && <Alert tone="danger">Не удалось выполнить проверку. Попробуйте ещё раз.</Alert>}
      {check.data && <Result r={check.data} />}
    </Panel>
  )
}

function Verdict({ ok, title, reasons }: { ok: boolean; title: string; reasons?: string[] }) {
  return (
    <div
      role={ok ? 'status' : 'alert'}
      data-testid={ok ? 'verdict-ok' : 'verdict-bad'}
      className={`flex gap-3 rounded-panel px-4 py-3.5 ${ok ? 'bg-tint' : 'bg-[#FFF1F3]'}`}
    >
      <span aria-hidden="true" className={`text-[22px] leading-none ${ok ? 'text-brand' : 'text-danger'}`}>
        {ok ? '✓' : '✕'}
      </span>
      <div className="flex flex-col gap-1">
        <p className="t-body-strong text-ink">{title}</p>
        {reasons && reasons.length > 0 && (
          <ul className="m-0 list-disc pl-5 t-body text-ink-2">
            {reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function Result({ r }: { r: CredentialVerify }) {
  const s = r.summary
  return (
    <div className="flex flex-col gap-4">
      <Verdict ok={r.valid} title={r.valid ? 'Паспорт подлинный' : 'Проверка не пройдена'} reasons={r.valid ? undefined : r.reasons} />
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0 t-small text-ink-2">
        {r.checks.map((c) => (
          <li key={c.id} className="flex gap-2">
            <span aria-hidden="true" className={c.ok ? 'text-brand' : 'text-danger'}>
              {c.ok ? '✓' : '✕'}
            </span>
            <span>{c.label}</span>
          </li>
        ))}
      </ul>
      {s && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 t-body">
          <dt className="text-ink-3">Паспорт</dt>
          <dd className="text-ink">{s.achievement || s.title}</dd>
          {s.holder_name && (
            <>
              <dt className="text-ink-3">Владелец</dt>
              <dd className="text-ink">{s.holder_name}</dd>
            </>
          )}
          {s.tested_on && (
            <>
              <dt className="text-ink-3">Подтверждено тестом</dt>
              <dd className="text-ink">{fmt(s.tested_on)}</dd>
            </>
          )}
          {s.results.map((x) => (
            <div key={x.kind} className="contents">
              <dt className="text-ink-3">{x.kind === 'sem' ? 'Погрешность (SEM)' : 'Балл'}</dt>
              <dd className="text-ink">{x.value}</dd>
            </div>
          ))}
          <dt className="text-ink-3">Выдан</dt>
          <dd className="text-ink">{fmt(s.issued_on)}</dd>
          <dt className="text-ink-3">Действует до</dt>
          <dd className="text-ink">{fmt(s.valid_until)}</dd>
          <dt className="text-ink-3">Издатель</dt>
          <dd className="text-ink">
            {s.issuer_name}
            <span className="block break-all t-small text-ink-3">{s.issuer_id}</span>
          </dd>
        </dl>
      )}
      <p className="t-small text-ink-3">{r.disclaimer}</p>
    </div>
  )
}
