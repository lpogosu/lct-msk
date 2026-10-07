import { useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { ApiException, errorMessage } from '@/api/errors'
import { ExpressResult } from '@/features/candidate/components/ExpressResult'
import { BlindCard } from '@/features/candidate/components/BlindCard'
import { ProbabilityBar, SectionTitle, StatRow } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { qk, type ResultOut } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { Alert, Button, Checkbox, MobileHeader, Panel, Ruler, TrustNode, useToast, type NodeKind } from '@/ui'

type Offer = NonNullable<ResultOut['offers']>[number]

/**
 * К5 «Результат теста»: решение и балл на линейке 300–600 с интервалом, «Насколько мы уверены», модули ✓/✓✓/~,
 * предложения добора вверх/вниз, разбор ошибок, превью «так вас видит работодатель» и согласие на показ слепой карточки.
 */
export default function CandidateResult() {
  const { sessionId = '' } = useParams()
  const q = useQuery({
    queryKey: qk.result(sessionId),
    queryFn: () => unwrap(api.GET('/api/tests/sessions/{session_id}/result', { params: { path: { session_id: sessionId } } })),
    retry: false,
    refetchOnMount: 'always',
  })

  // «тест ещё идёт» (409) — возвращаем в плеер
  if (q.error instanceof ApiException && q.error.code === 'result_not_ready') {
    return <Navigate to={`/candidate/test/${sessionId}`} replace />
  }

  return (
    <>
      <MobileHeader title="Результат теста" backTo="/candidate" />
      <QueryBoundary query={q} errorTitle="Не удалось загрузить результат">
        {(r) => <ResultView r={r} sessionId={sessionId} />}
      </QueryBoundary>
    </>
  )
}

const MODULE_NODE: Record<string, NodeKind> = { strong: 'fsp', confirmed: 'test', insufficient: 'claimed', none: 'resume' }

function ResultView({ r, sessionId }: { r: ResultOut; sessionId: string }) {
  if (!r.visible) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="t-title text-brand">Результат пока скрыт</h1>
        <Panel variant="tint" radius="lg" padding="md">
          <p className="t-body text-ink">{r.message ?? 'Результат появится после проверки.'}</p>
        </Panel>
        <Button block to="/candidate">
          На главный экран
        </Button>
      </div>
    )
  }

  if (r.express) return <ExpressResult r={r} sessionId={sessionId} />

  const decision = r.decision
  const passed = !!decision?.passed
  const gradeName = r.confirmed_grade?.name ?? r.session.target_grade.name
  const interval = r.interval80 && r.interval80.length === 2 ? ([r.interval80[0], r.interval80[1]] as const) : undefined
  const certainty = r.certainty ?? []
  const modules = r.modules ?? []
  const offers = r.offers ?? []

  return (
    <div className="flex flex-col gap-8">
      <header>
        <p className="mb-1 t-body text-ink-3">{r.session.specialization.label}</p>
        <h1>
          <span className={cn('block font-mono text-[40px] font-medium leading-[48px] tracking-[-1px]', passed ? 'text-brand' : 'text-ink')}>
            {gradeName}
          </span>
          <span className="mt-1 block t-lead text-ink">
            {passed ? (decision?.borderline ? 'подтверждён на границе' : 'подтверждён тестом') : 'пока не подтверждён'}
          </span>
        </h1>
        {r.headline && <p className="sr-only">{r.headline}</p>}
      </header>

      {r.score != null && (
        <div className="flex flex-col gap-5">
          <Ruler size="full" value={r.score} interval={interval} />
          <StatRow
            items={[
              { value: r.score, label: 'балл' },
              ...(r.confidence_pct != null ? [{ value: `${r.confidence_pct}%`, label: 'уверенность' }] : []),
              { value: r.n_shown, label: 'заданий' },
            ]}
          />
        </div>
      )}

      {certainty.length > 0 && (
        <section aria-labelledby="sure-h">
          <SectionTitle>
            <span id="sure-h">Насколько мы уверены</span>
          </SectionTitle>
          <ProbabilityBar
            label="Вероятности уровней"
            parts={certainty
              .filter((c) => c.percent > 0)
              .map((c) => ({ name: c.name, percent: c.percent, active: c.grade === r.session.target_grade.code }))}
          />
          {r.stop_text && <p className="mt-3 t-body text-ink-2">{r.stop_text}</p>}
          {r.by_level_text && <p className="mt-2 t-small text-ink-3">Решено по уровням: {r.by_level_text}</p>}
        </section>
      )}

      {modules.length > 0 && (
        <section aria-labelledby="mods-h">
          <SectionTitle>
            <span id="mods-h">Модули теста</span>
          </SectionTitle>
          <ul>
            {modules.map((m) => (
              <li key={m.module} className="flex items-center gap-3 border-b border-hairline py-3 first:pt-0">
                <TrustNode kind={MODULE_NODE[m.tag] ?? 'resume'} size={16} />
                <span className="min-w-0 flex-1 t-body text-ink">{m.name}</span>
                <span className="shrink-0 text-right t-small text-ink-3">
                  {m.tag_ru}
                  {m.tag === 'none' || m.tag === 'insufficient' ? '' : ` · ${m.correct} из ${m.n}`}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 t-small text-ink-3">
            Залитый узел — сильный уровень, кольцо — подтверждено, пунктир — мало заданий для вывода.
          </p>
        </section>
      )}

      {!passed && r.message && (
        <Panel variant="tint" radius="lg" padding="md" className="flex flex-col gap-2">
          <p className="t-label text-ink">Когда можно пересдать</p>
          <p className="t-body text-ink">{r.message}</p>
          <p className="t-small text-ink-3">
            Неподтверждённый грейд не сжигает попытку на грейд ниже, а неудачное повышение не снижает подтверждённый.
          </p>
        </Panel>
      )}

      {offers.length > 0 && <Offers offers={offers} sessionId={sessionId} expireIn={r.offers_expire_in} keepGrade={passed ? gradeName : null} />}

      {r.review_available && (
        <Panel variant="panel" radius="md" padding="sm" className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="t-label text-ink">Разбор ошибок</p>
            <p className="t-small text-ink-3">Ваши задания, верные ответы и объяснения.</p>
          </div>
          <Button variant="outline" size="sm" to={`/candidate/result/${sessionId}/review`}>
            Открыть
          </Button>
        </Panel>
      )}

      {passed && <BlindSection r={r} />}

      <div className="flex flex-col gap-2">
        <Button block size="lg" to="/candidate">
          Продолжить
        </Button>
        <Link to="/candidate/qualification" className="self-center t-label text-brand underline-offset-4 hover:underline">
          Мой грейд: история и следующий тест
        </Link>
      </div>
    </div>
  )
}

// --- Добор ---------------------------------------------------------------------------------------------------------

function Offers({ offers, sessionId, expireIn, keepGrade }: { offers: Offer[]; sessionId: string; expireIn?: string | null; keepGrade: string | null }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)

  const act = useMutation({
    mutationFn: (v: { decision: 'accept' | 'decline'; direction?: 'up' | 'down' }) =>
      unwrap(
        api.POST('/api/tests/sessions/{session_id}/extension', {
          params: { path: { session_id: sessionId } },
          body: { decision: v.decision, direction: v.direction ?? null },
        }),
      ),
    onSuccess: (res, v) => {
      void qc.invalidateQueries({ queryKey: qk.qualifications })
      void qc.invalidateQueries({ queryKey: qk.result(sessionId) })
      if (v.decision === 'decline' || !res.child) {
        toast.show({ message: res.message })
        return
      }
      navigate(res.child.finished ? `/candidate/result/${res.child.id}` : `/candidate/test/${res.child.id}`)
    },
    onError: (e) => setError(errorMessage(e)),
  })

  return (
    <section aria-labelledby="ext-h" className="flex flex-col gap-3">
      {offers.map((o) => {
        const up = o.direction === 'up'
        return (
          <Panel key={o.direction} variant="tint" radius="lg" padding="md" className="flex flex-col gap-3" aria-labelledby={`ext-${o.direction}`}>
            <h2 id={`ext-${o.direction}`} className="t-name text-ink">
              {up ? `Можно попробовать ${o.target_grade.name} сейчас` : o.instant ? `Подтвердить ${o.target_grade.name}` : `Добор на ${o.target_grade.name}`}
            </h2>
            <p className="t-body text-ink-2">
              {o.text}
              {up && keepGrade ? ` Ваш ${keepGrade} останется в любом случае.` : ''}
            </p>
            {!o.instant && <p className="t-small text-ink-3">До {o.max_items} заданий · уверенность по уже данным ответам: {o.confidence_pct}%</p>}
            <Button
              variant="outline"
              block
              loading={act.isPending && act.variables?.direction === o.direction && act.variables.decision === 'accept'}
              disabled={act.isPending}
              onClick={() => {
                setError(null)
                act.mutate({ decision: 'accept', direction: up ? 'up' : 'down' })
              }}
            >
              {o.instant ? `Подтвердить ${o.target_grade.name}` : `Попробовать ${o.target_grade.name}`}
            </Button>
          </Panel>
        )
      })}
      {error && <Alert tone="danger">{error}</Alert>}
      <p className="t-small text-ink-3" id="ext-h">
        Решить можно {expireIn ?? 'в течение суток'}. Отказаться тоже можно:{' '}
        <button
          type="button"
          className="text-brand underline-offset-4 hover:underline disabled:opacity-50"
          disabled={act.isPending}
          onClick={() => act.mutate({ decision: 'decline' })}
        >
          оставить результат как есть
        </button>
        .
      </p>
    </section>
  )
}

// --- Слепая карточка и согласие -------------------------------------------------------------------------------

function BlindSection({ r }: { r: ResultOut }) {
  const qc = useQueryClient()
  const toast = useToast()
  const consent = r.blind_card_consent
  const [agree, setAgree] = useState(false)
  const grant = useMutation({
    mutationFn: () => unwrap(api.POST('/api/consents', { body: { purpose: 'blind_card', text_version: consent.version } })),
    onSuccess: () => {
      toast.success('Готово: работодатели видят вашу слепую карточку.')
      void qc.invalidateQueries({ queryKey: qk.result(r.session.id) })
      void qc.invalidateQueries({ queryKey: qk.privacy })
      void qc.invalidateQueries({ queryKey: qk.consents })
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  const p = r.blind_card_preview

  return (
    <section aria-labelledby="blind-h" className="flex flex-col gap-4">
      <SectionTitle>
        <span id="blind-h">Так вас видит работодатель</span>
      </SectionTitle>
      {p ? (
        <BlindCard
          data={{
            pseudonym: p.pseudonym,
            category: p.category,
            badge: p.badge,
            interval: p.interval80 && p.interval80.length === 2 ? [p.interval80[0], p.interval80[1]] : null,
            modules: (p.modules ?? []).map((m) => ({ name: m.name, symbol: m.symbol })),
            freshness: p.freshness,
            trust: p.trust,
            how: p.how_obtained,
            hidden: p.note,
          }}
        />
      ) : (
        <p className="t-body text-ink-2">Превью появится после подтверждения категории.</p>
      )}

      {consent.given ? (
        <p className="t-small text-ink-3">
          Вы разрешили показывать слепую карточку (версия текста {consent.version}). Отозвать согласие можно в{' '}
          <Link to="/candidate/consents" className="text-brand underline-offset-4 hover:underline">
            журнале согласий
          </Link>
          .
        </p>
      ) : (
        <Panel variant="panel" radius="md" padding="sm" className="flex flex-col gap-3">
          <Checkbox
            checked={agree}
            onChange={(e) => setAgree(e.target.checked)}
            label={<span className="t-body-strong">{consent.title}</span>}
            description={consent.summary}
          />
          <Button block disabled={!agree} loading={grant.isPending} onClick={() => grant.mutate()}>
            Показывать меня работодателям
          </Button>
        </Panel>
      )}
    </section>
  )
}
