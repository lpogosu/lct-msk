import { Link } from 'react-router-dom'
import { SectionTitle, StatRow } from '@/features/candidate/components/Bits'
import type { ResultOut } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { Alert, Button, Panel, Ruler } from '@/ui'

/**
 * Результат экспресс-версии для жюри. Честная подача: это оценка с широкой погрешностью, а не категория. Если точности
 * не хватило — прямо «грейд не определён точно, нужен полный тест», без вымученного грейда. Внизу — чем отличается
 * полный тест. Данные приходят из `result.express` (backend/app/services/testing/express.py, тексты — config/testing.yaml).
 */
export function ExpressResult({ r, sessionId }: { r: ResultOut; sessionId: string }) {
  const ex = r.express!
  const interval = r.interval80 && r.interval80.length === 2 ? ([r.interval80[0], r.interval80[1]] as const) : undefined

  return (
    <div className="flex flex-col gap-8" data-testid="express-result">
      <header className="flex flex-col gap-3">
        <p className="inline-flex w-fit items-center rounded-full bg-tint px-3 py-1 t-label text-brand">{ex.badge}</p>
        <p className="t-body text-ink-3">{r.session.specialization.label}</p>
        <h1 className={cn('t-title', ex.verdict === 'estimate_pass' ? 'text-brand' : 'text-ink')} data-testid="express-headline">
          {ex.headline}
        </h1>
        <p className="t-body text-ink-2">{ex.note}</p>
      </header>

      {r.score != null && (
        <div className="flex flex-col gap-5">
          <Ruler size="full" value={r.score} interval={interval} />
          <StatRow
            items={[
              { value: r.score, label: 'балл' },
              { value: ex.interval_width, label: 'ширина интервала' },
              { value: ex.n_items, label: 'заданий' },
            ]}
          />
          <Alert tone="info" title="Погрешность шире, чем у полного теста">
            {ex.margin_note}
          </Alert>
        </div>
      )}

      <section aria-labelledby="full-h">
        <SectionTitle>
          <span id="full-h">{ex.full_test_title}</span>
        </SectionTitle>
        <ul className="flex flex-col gap-2">
          {ex.full_test.map((line) => (
            <li key={line} className="t-body text-ink-2">
              {line}
            </li>
          ))}
        </ul>
      </section>

      <div className="flex flex-col gap-2">
        <Button block size="lg" to="/candidate/grade">
          Пройти полный тест
        </Button>
        {r.review_available && (
          <Button block variant="outline" to={`/candidate/result/${sessionId}/review`}>
            Разбор ошибок
          </Button>
        )}
        <Link to="/jury" className="self-center t-label text-brand underline-offset-4 hover:underline">
          Вернуться к выбору для жюри
        </Link>
      </div>
      <Panel variant="panel" radius="md" padding="sm">
        <p className="t-small text-ink-3">
          Экспресс-сессия помечена demo_assisted: в калибровку банка, метрики лаборатории и подбор работодателям она не
          попадает.
        </p>
      </Panel>
    </div>
  )
}
