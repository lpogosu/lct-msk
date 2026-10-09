import { useState } from 'react'
import { errorMessage } from '@/api/errors'
import { Alert, Button, Segmented, Skeleton } from '@/ui'
import { downloadTops, useCheckBrief, useCheckSummary, type Source } from '../api'
import { BriefDetail } from './BriefDetail'
import { BriefTable, MetricsBlock, SummaryHead } from './Summary'

/**
 * Набор целиком: сводка с метриками и брифами → бриф с топом. Открытый бриф и режим разбора живут в адресе страницы
 * (их передаёт родитель), поэтому ссылку на конкретный бриф можно переслать.
 */
export function SetView({
  source,
  briefId,
  onBrief,
  useGold,
  onGold,
  goldAvailable = true,
}: {
  source: Source
  briefId: string | null
  onBrief: (id: string | null) => void
  useGold: boolean
  onGold: (v: boolean) => void
  goldAvailable?: boolean
}) {
  const summary = useCheckSummary(source, useGold)
  const brief = useCheckBrief(source, briefId, useGold)
  const [csvBusy, setCsvBusy] = useState(false)
  const [csvError, setCsvError] = useState<string | null>(null)

  async function csv() {
    setCsvBusy(true)
    setCsvError(null)
    try {
      await downloadTops(source, useGold)
    } catch (e) {
      setCsvError(errorMessage(e))
    } finally {
      setCsvBusy(false)
    }
  }

  const toolbar = (
    <div className="flex flex-wrap items-center gap-3">
      {goldAvailable && (
        <Segmented
          aria-label="Разбор брифа"
          options={[
            { value: 'parser', label: 'Разбор текста, как в продукте' },
            { value: 'gold', label: 'Эталон набора' },
          ]}
          value={useGold ? 'gold' : 'parser'}
          onChange={(v) => onGold(v === 'gold')}
        />
      )}
      <Button variant="outline" size="sm" loading={csvBusy} onClick={() => void csv()}>
        Скачать CSV топов
      </Button>
    </div>
  )

  if (briefId) {
    return (
      <div className="flex flex-col gap-6">
        {toolbar}
        <BriefDetail
          briefId={briefId}
          detail={brief.data}
          pending={brief.isPending}
          error={brief.error}
          onBack={() => onBrief(null)}
          onRetry={() => void brief.refetch()}
        />
      </div>
    )
  }

  if (summary.isPending) {
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Считаем метрики">
        <p className="t-small text-ink-2">Ранжируем кандидатов по каждому брифу и считаем метрики — несколько секунд…</p>
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-14 w-full rounded-panel" />
        ))}
      </div>
    )
  }
  if (summary.isError || !summary.data) {
    return (
      <Alert tone="danger" title="Не удалось посчитать метрики">
        <span>{errorMessage(summary.error)}</span>{' '}
        <Button variant="text" onClick={() => void summary.refetch()}>
          Повторить
        </Button>
      </Alert>
    )
  }
  const s = summary.data
  return (
    <div className="flex flex-col gap-8">
      <SummaryHead summary={s} />
      {toolbar}
      {csvError && <Alert tone="danger">{csvError}</Alert>}
      {s.warnings.length > 0 && (
        <details className="t-small text-ink-2">
          <summary className="cursor-pointer text-ink">Предупреждения импорта ({s.warnings.length})</summary>
          <ul className="m-0 mt-2 flex list-disc flex-col gap-1 pl-5">
            {s.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </details>
      )}
      <MetricsBlock summary={s} />
      <BriefTable summary={s} onOpen={(id) => onBrief(id)} />
    </div>
  )
}
