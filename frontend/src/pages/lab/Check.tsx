import { useSearchParams } from 'react-router-dom'
import { HowToRead } from '@/features/jurycheck/components/common'
import { SetView } from '@/features/jurycheck/components/SetView'
import { UploadTab } from '@/features/jurycheck/components/UploadTab'
import { VacancyTab } from '@/features/jurycheck/components/VacancyTab'
import { Tabs } from '@/ui/Tabs'
import { LabHead } from './parts'

type Tab = 'pairs' | 'vacancy' | 'own'

const TABS: { value: Tab; label: string }[] = [
  { value: 'pairs', label: 'Наш набор пар' },
  { value: 'vacancy', label: 'Своя вакансия против банка платформы' },
  { value: 'own', label: 'Свой набор' },
]

const LEAD: Record<Tab, string> = {
  pairs:
    'Тестовый набор пар «бриф × кандидат» из репозитория с разметкой релевантности: метрики считаются заново на этом стенде, а топ каждого брифа можно проверить руками.',
  vacancy:
    'Вставьте свою вакансию с вилкой — подберём кандидатов из банка платформы тем же движком, что у работодателя. Выдача слепая: без имён и контактов.',
  own: 'Загрузите свои вакансии, кандидатов и разметку — получите те же метрики и топ по каждой вакансии, плюс CSV.',
}

/**
 * «Проверка подбора на ваших данных» (/lab/check, методист и вход жюри «Лаборатория»). Вкладка, открытый бриф,
 * загрузка и режим разбора — в адресе: ссылку на конкретный бриф можно переслать.
 */
export default function Check() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: Tab = raw === 'vacancy' || raw === 'own' ? raw : 'pairs'
  const brief = params.get('brief')
  const batch = params.get('batch')
  const gold = params.get('gold') === '1'

  function update(patch: Record<string, string | null>) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [k, v] of Object.entries(patch)) {
          if (v == null || v === '') next.delete(k)
          else next.set(k, v)
        }
        return next
      },
      { replace: false },
    )
  }

  return (
    <div className="flex flex-col gap-8">
      <LabHead caption="Лаборатория · для жюри" title="Проверка подбора на ваших данных" lead={LEAD[tab]} />
      <Tabs
        aria-label="Сценарий проверки"
        options={TABS}
        value={tab}
        onChange={(t) => update({ tab: t === 'pairs' ? null : t, brief: null, gold: null })}
      />
      <HowToRead />
      {tab === 'pairs' && (
        <SetView
          source={{ kind: 'repo' }}
          briefId={brief}
          onBrief={(id) => update({ brief: id })}
          useGold={gold}
          onGold={(v) => update({ gold: v ? '1' : null })}
        />
      )}
      {tab === 'vacancy' && <VacancyTab />}
      {tab === 'own' && (
        <UploadTab
          batchId={batch}
          onBatch={(id) => update({ batch: id, brief: null })}
          briefId={brief}
          onBrief={(id) => update({ brief: id })}
          useGold={gold}
          onGold={(v) => update({ gold: v ? '1' : null })}
        />
      )}
    </div>
  )
}
