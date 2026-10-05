import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { SectionTitle, StatRow } from '@/features/candidate/components/Bits'
import { QueryBoundary } from '@/features/candidate/components/QueryBoundary'
import { qk } from '@/features/candidate/queries'
import { Alert, Button, MobileHeader, Panel } from '@/ui'

/** К15 «Как меня видят» [доп.]: показы в подборках за 7 дней, позиция, чего чаще всего не хватает, причины отказов компаний. */
export default function CandidateVisibility() {
  const q = useQuery({ queryKey: qk.visibility, queryFn: () => unwrap(api.GET('/api/candidates/me/visibility-stats')) })
  return (
    <>
      <MobileHeader title="Как меня видят" backTo="/candidate/profile" />
      <QueryBoundary query={q}>
        {(s) => (
          <div className="flex flex-col gap-8">
            <header>
              <h1 className="t-title text-brand">Как меня видят</h1>
              <p className="mt-2 t-body text-ink-2">За последние {s.period_days} дней. Компании не видят этих цифр — только вы.</p>
            </header>

            <Panel variant="tint" radius="lg" padding="md">
              <p className="t-body text-ink">{s.message}</p>
            </Panel>

            {s.enough_data && (
              <>
                <StatRow
                  items={[
                    { value: s.impressions, label: 'показов' },
                    { value: s.top10, label: 'в топ-10' },
                    { value: s.profile_views, label: 'открыли' },
                  ]}
                />
                <dl className="flex flex-wrap gap-x-8 gap-y-3">
                  <div>
                    <dt className="t-small text-ink-3">В подборках</dt>
                    <dd className="t-num-m text-ink">{s.selections}</dd>
                  </div>
                  {s.median_position != null && (
                    <div>
                      <dt className="t-small text-ink-3">Медианная позиция</dt>
                      <dd className="t-num-m text-ink">{s.median_position}</dd>
                    </div>
                  )}
                  <div>
                    <dt className="t-small text-ink-3">Приглашений</dt>
                    <dd className="t-num-m text-ink">{s.invitations}</dd>
                  </div>
                </dl>
              </>
            )}

            {s.missing_skill && (
              <section>
                <SectionTitle as="h3">Чаще всего не хватает</SectionTitle>
                <Panel variant="panel" radius="lg" padding="sm" className="flex flex-col gap-2">
                  <p className="t-body-strong text-ink">{s.missing_skill.name}</p>
                  <p className="t-small text-ink-2">Обязательный навык в подборках: {s.missing_skill.count}, но не подтверждён.</p>
                  <p className="t-small text-ink-3">{s.missing_skill.action}</p>
                  <div>
                    <Button size="sm" to="/candidate/tasks">
                      К задачам
                    </Button>
                  </div>
                </Panel>
              </section>
            )}

            {(s.decline_reasons ?? []).length > 0 && (
              <section>
                <SectionTitle as="h3">Причины отказов компаний</SectionTitle>
                <ul className="flex flex-col gap-2">
                  {s.decline_reasons!.map((r) => (
                    <li key={r.reason} className="flex items-baseline justify-between gap-3 border-b border-hairline pb-2 t-body text-ink">
                      <span>{r.reason}</span>
                      <span className="font-mono text-ink-2">{r.count}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {!s.enough_data && <Alert>Данных пока мало: цифры появятся, когда вас покажут в подборках не меньше пяти раз.</Alert>}
          </div>
        )}
      </QueryBoundary>
    </>
  )
}
