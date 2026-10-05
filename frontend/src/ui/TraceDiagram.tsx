import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { TrustNode, type NodeKind } from './TrustNode'

/**
 * Что стоит на правом конце трассы:
 *   fsp | test | resume | claimed — узел доверия (форма = чем подтверждено);
 *   open — пустое кольцо (смысловая трасса брифа: «слово из текста → как поняли»);
 *   none — «нет данных»: серая точка, пунктирная линия.
 */
export type TraceEnd = NodeKind | 'open' | 'none'

export interface TraceDiagramRow {
  /** Левая подпись: требование («Python») или цитата из брифа */
  label: ReactNode
  /** Правая часть: чем подтверждено / как поняли */
  evidence: ReactNode
  end: TraceEnd
  /** danger — «не подтверждена» (красный текст), muted — «нет данных» */
  tone?: 'default' | 'danger' | 'muted'
  key?: string
}

export interface TraceDiagramProps {
  rows: TraceDiagramRow[]
  /** Ширина левой колонки; число — px */
  labelWidth?: number | string
  /** Цвет заливки колец узлов — как у фона, на котором лежит диаграмма (panel или white) */
  surface?: 'panel' | 'white'
  /** Левые подписи моноширинным шрифтом (смысловая трасса брифа, Figma 40:83) */
  monoLabels?: boolean
  /** Высота строки, px: 34 — «Что вы просили», 44 — «Как мы поняли» */
  rowHeight?: number
  className?: string
  'aria-label'?: string
}

const SURFACE_HEX = { panel: '#F6F6F8', white: '#FFFFFF' } as const

// Линия трассы: подтверждено ФСП/тестом — фиолетовая; из резюме — серая сплошная; заявлено и «нет данных» — пунктир steel.
function lineKind(end: TraceEnd): 'brand' | 'ink3' | 'dashed' {
  if (end === 'fsp' || end === 'test' || end === 'open') return 'brand'
  if (end === 'resume') return 'ink3'
  return 'dashed'
}

/**
 * Диаграмма «требование → доказательство»: слева подписи, справа свидетельства, между ними — трасса
 * от начального узла к узлу доверия. Figma 28:51 «Трассы требований» и 40:83 «Трассы смысла».
 */
export function TraceDiagram({
  rows,
  labelWidth = 100,
  surface = 'panel',
  monoLabels,
  rowHeight = 34,
  className,
  ...aria
}: TraceDiagramProps) {
  const style = {
    '--lw': typeof labelWidth === 'number' ? `${labelWidth}px` : labelWidth,
    '--rh': `${rowHeight}px`,
  } as CSSProperties
  const fill = SURFACE_HEX[surface]

  return (
    <ul className={cn('m-0 list-none p-0', className)} style={style} aria-label={aria['aria-label']}>
      {rows.map((row, i) => {
        const kind = lineKind(row.end)
        const isNode = row.end !== 'none' && row.end !== 'open'
        return (
          <li
            key={row.key ?? i}
            // на телефоне подпись идёт отдельной строкой над трассой и свидетельством, с sm — три колонки как в Figma
            className={cn(
              'grid items-center gap-x-2 gap-y-0.5 py-1.5 sm:gap-x-2.5 sm:py-0',
              "[grid-template-columns:56px_minmax(0,1fr)] [grid-template-areas:'label_label'_'trace_evidence']",
              "sm:[grid-template-columns:minmax(0,var(--lw))_96px_minmax(0,1fr)] sm:[grid-template-areas:'label_trace_evidence']",
            )}
            style={{ minHeight: 'var(--rh)' }}
          >
            <span className={cn('min-w-0 break-words [grid-area:label]', monoLabels ? 't-code text-ink-2' : 't-label text-ink')}>
              {row.label}
            </span>
            {/* трасса */}
            <span className="relative block h-5 [grid-area:trace]" aria-hidden="true">
              <span
                className={cn(
                  'absolute left-1 right-2.5 top-1/2 h-[1.5px] -translate-y-1/2',
                  kind === 'brand' && 'bg-brand',
                  kind === 'ink3' && 'bg-ink-3',
                  kind === 'dashed' && 'trace-dashed',
                )}
              />
              <span
                className={cn(
                  'absolute left-0.5 top-1/2 h-[7px] w-[7px] -translate-y-1/2 rounded-full',
                  kind === 'dashed' ? 'bg-steel' : kind === 'ink3' ? 'bg-ink-3' : 'bg-brand',
                )}
              />
              <span className="absolute right-0 top-1/2 -translate-y-1/2">
                {isNode ? (
                  <TrustNode kind={row.end as NodeKind} size={20} surface={fill} />
                ) : row.end === 'open' ? (
                  <span
                    className="block h-[11px] w-[11px] rounded-full border-[1.5px] border-brand"
                    style={{ background: fill }}
                  />
                ) : (
                  <span className="block h-1.5 w-1.5 rounded-full bg-steel" />
                )}
              </span>
            </span>
            <span
              className={cn(
                't-body min-w-0 [grid-area:evidence]',
                row.tone === 'danger' && 'text-danger-text',
                row.tone === 'muted' && 'text-ink-3',
                (!row.tone || row.tone === 'default') && 'text-ink',
              )}
            >
              {row.evidence}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
