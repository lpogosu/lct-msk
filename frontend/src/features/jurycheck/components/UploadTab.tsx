import { useRef, useState } from 'react'
import { errorMessage } from '@/api/errors'
import { formatInt } from '@/lib/format'
import { Alert, Button, Checkbox, EmptyState, Input, Panel, Skeleton } from '@/ui'
import {
  downloadTemplate,
  useDeleteImport,
  useImportPairs,
  useImportSimple,
  useImports,
  type ImportResult,
  type TemplateName,
} from '../api'
import { planUpload, readPicked, ROLE_LABEL, type PickedFile } from '../files'
import { SetView } from './SetView'

const WHEN = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' })

function formatDateTime(v: string): string {
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : WHEN.format(d)
}

const TEMPLATES: { name: TemplateName; label: string }[] = [
  { name: 'vacancies.csv', label: 'Вакансии (CSV)' },
  { name: 'candidates.csv', label: 'Кандидаты (CSV)' },
  { name: 'labels.csv', label: 'Разметка (CSV)' },
  { name: 'example.json', label: 'Всё одним JSON' },
]

/** «Свой набор»: файлы по шаблону (CSV/JSON) или строки набора пар (JSONL) → импорт → метрики и топы по вакансиям. */
export function UploadTab({
  batchId,
  onBatch,
  briefId,
  onBrief,
  useGold,
  onGold,
}: {
  batchId: string | null
  onBatch: (id: string | null) => void
  briefId: string | null
  onBrief: (id: string | null) => void
  useGold: boolean
  onGold: (v: boolean) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [picked, setPicked] = useState<PickedFile[]>([])
  const [reading, setReading] = useState(false)
  const [name, setName] = useState('')
  const [zeroUnlabeled, setZeroUnlabeled] = useState(true)
  const [tplError, setTplError] = useState<string | null>(null)
  const [last, setLast] = useState<ImportResult | null>(null)
  const imports = useImports()
  const importPairs = useImportPairs()
  const importSimple = useImportSimple()
  const del = useDeleteImport()
  const plan = planUpload(picked)
  const busy = importPairs.isPending || importSimple.isPending
  const uploadError = importPairs.error ?? importSimple.error

  async function pick(files: FileList | null) {
    if (!files || files.length === 0) return
    setReading(true)
    importPairs.reset()
    importSimple.reset()
    try {
      const read = await Promise.all(Array.from(files).map(readPicked))
      setPicked((prev) => [...prev.filter((p) => !read.some((r) => r.name === p.name)), ...read])
    } finally {
      setReading(false)
    }
  }

  async function upload() {
    const title = name.trim() || picked.map((p) => p.name).join(', ').slice(0, 200)
    let res: ImportResult
    if (plan.kind === 'pairs') res = await importPairs.mutateAsync({ name: title, pairs: plan.rows })
    else if (plan.kind === 'simple') {
      res = await importSimple.mutateAsync({
        name: title,
        vacancies: (plan.tables.vacancies ?? null) as string | Record<string, unknown>[] | null,
        candidates: (plan.tables.candidates ?? null) as string | Record<string, unknown>[] | null,
        labels: (plan.tables.labels ?? null) as string | Record<string, unknown>[] | null,
        unlabeled_as_irrelevant: zeroUnlabeled,
      })
    } else return
    setLast(res)
    setPicked([])
    onBrief(null)
    onBatch(res.batch_id)
  }

  async function template(n: TemplateName) {
    setTplError(null)
    try {
      await downloadTemplate(n)
    } catch (e) {
      setTplError(errorMessage(e))
    }
  }

  const list = imports.data ?? []
  const current = list.find((b) => b.id === batchId) ?? null

  return (
    <div className="flex flex-col gap-8">
      <Panel variant="panel" radius="xl" padding="lg" className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <h2 className="t-name text-ink">Загрузить свой набор</h2>
          <p className="t-body text-ink-2">
            Три таблицы по шаблону — вакансии (<span className="font-mono">id, text, salary_from, salary_to, salary_net</span>),
            кандидаты (<span className="font-mono">id, spec, grade, skills</span> через «;»,{' '}
            <span className="font-mono">experience_months, self_description</span>, необязательно{' '}
            <span className="font-mono">fsp</span>) и разметка (<span className="font-mono">vacancy_id, candidate_id, rel</span>{' '}
            0–3) — в CSV или одним JSON. Или строки набора пар в JSONL, как в репозитории. Данные попадают только в вашу загрузку,
            не в банк платформы.
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="t-small text-ink-3">Шаблоны с примером на 3 вакансии:</span>
            {TEMPLATES.map((t) => (
              <Button key={t.name} variant="text" onClick={() => void template(t.name)}>
                {t.label}
              </Button>
            ))}
          </div>
          {tplError && <Alert tone="danger">{tplError}</Alert>}
        </div>

        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".csv,.tsv,.txt,.json,.jsonl,.ndjson,text/csv,application/json"
          className="sr-only"
          aria-label="Выбрать файлы набора"
          onChange={(e) => {
            void pick(e.target.files)
            e.target.value = ''
          }}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button variant={picked.length ? 'outline' : 'primary'} loading={reading} onClick={() => fileRef.current?.click()}>
            {picked.length ? 'Добавить файлы' : 'Выбрать файлы'}
          </Button>
          {picked.length > 0 && (
            <Button variant="text" onClick={() => setPicked([])}>
              Очистить
            </Button>
          )}
        </div>

        {picked.length > 0 && (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {picked.map((f) => (
              <li key={f.name} className="flex flex-wrap items-baseline gap-x-3 t-small">
                <span className="font-mono text-ink">{f.name}</span>
                {f.error ? (
                  <span className="text-danger-text">{f.error}</span>
                ) : (
                  <span className="text-ink-2">
                    {f.role ? ROLE_LABEL[f.role] : ''} · {formatInt(f.rows)} строк
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {plan.kind === 'error' && <Alert tone="danger">{plan.message}</Alert>}

        {(plan.kind === 'pairs' || plan.kind === 'simple') && (
          <div className="flex flex-col gap-4">
            <Input label="Название загрузки" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} placeholder="Например: набор жюри, вариант 1" />
            {plan.kind === 'simple' && plan.tables.labels !== undefined && (
              <Checkbox
                label="Неразмеченные пары считать нерелевантными (rel 0)"
                description="Тогда для размеченной вакансии ранжируются все кандидаты набора, и неразмеченный кандидат в топе — промах. Без флажка ранжируются только размеченные."
                checked={zeroUnlabeled}
                onChange={(e) => setZeroUnlabeled(e.target.checked)}
              />
            )}
            <div>
              <Button loading={busy} onClick={() => void upload().catch(() => undefined)}>
                Загрузить и посчитать
              </Button>
            </div>
          </div>
        )}
        {uploadError && (
          <Alert tone="danger" title="Набор не загружен">
            {errorMessage(uploadError)}
          </Alert>
        )}
        {last && !uploadError && (
          <Alert tone="success" title={`Загружено: ${last.name}`}>
            Вакансий {formatInt(last.briefs)}, кандидатов {formatInt(last.candidates)} (без теста {formatInt(last.unconfirmed)}),
            размеченных пар {formatInt(last.pairs)}. {last.note}
          </Alert>
        )}
      </Panel>

      <section aria-labelledby="mine-h" className="flex flex-col gap-3">
        <h2 id="mine-h" className="t-name text-ink">
          Мои загрузки
        </h2>
        {imports.isPending ? (
          <Skeleton className="h-14 w-full rounded-panel" />
        ) : imports.isError ? (
          <Alert tone="danger">{errorMessage(imports.error)}</Alert>
        ) : list.length === 0 ? (
          <p className="t-body text-ink-2">Пока ничего не загружено. Начните с шаблона — в нём пример на 3 вакансии.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col p-0">
            {list.map((b) => (
              <li key={b.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline py-2.5 last:border-0">
                <button
                  type="button"
                  onClick={() => {
                    onBrief(null)
                    onBatch(b.id)
                  }}
                  className="min-w-0 text-left"
                  aria-current={b.id === batchId ? 'true' : undefined}
                >
                  <span className={b.id === batchId ? 't-body-strong text-brand' : 't-body text-ink hover:text-brand'}>{b.name}</span>
                  <span className="block t-small text-ink-3">
                    {formatDateTime(b.created_at)} · вакансий {formatInt(b.briefs)} · кандидатов {formatInt(b.candidates)} · пар{' '}
                    {formatInt(b.pairs)}
                  </span>
                </button>
                <Button
                  variant="text"
                  disabled={del.isPending}
                  onClick={() => {
                    if (!window.confirm(`Удалить загрузку «${b.name}»? Данные платформы не затрагиваются.`)) return
                    del.mutate(b.id, {
                      onSuccess: () => {
                        if (b.id === batchId) onBatch(null)
                      },
                    })
                  }}
                >
                  Удалить
                </Button>
              </li>
            ))}
          </ul>
        )}
        {del.isError && <Alert tone="danger">{errorMessage(del.error)}</Alert>}
      </section>

      {batchId ? (
        <SetView
          key={batchId}
          source={{ kind: 'batch', batchId }}
          briefId={briefId}
          onBrief={onBrief}
          useGold={useGold}
          onGold={onGold}
          goldAvailable={current?.format === 'pairs'}
        />
      ) : (
        list.length > 0 && (
          <Panel variant="panel" radius="lg">
            <EmptyState title="Выберите загрузку" description="Нажмите на загрузку в списке выше — покажем метрики и топы по вакансиям." size="compact" />
          </Panel>
        )
      )}
    </div>
  )
}
