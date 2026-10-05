import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schemas } from '@/api/types'
import { qk } from '@/features/candidate/queries'
import { cn } from '@/lib/cn'
import { Alert, Button, Skeleton } from '@/ui'
import { Sheet } from './Sheet'

type Item = Schemas['NotificationOut']

function linkFor(n: Item): string | null {
  if (n.kind === 'invitation' && n.entity_id) return `/candidate/invitations/${n.entity_id}`
  if (n.kind === 'application') return '/candidate/vacancies'
  if (n.kind === 'task') return '/candidate/tasks'
  return null
}

/** Колокольчик на главном экране: уведомления кабинета (приглашения, отклики, задачи) и «прочитано». */
export function NotificationsBell() {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const q = useQuery({
    queryKey: qk.notifications,
    queryFn: () => unwrap(api.GET('/api/notifications', { params: { query: { limit: 30 } } })),
    refetchInterval: 60_000,
  })
  const unread = q.data?.unread.total ?? 0

  const readOne = useMutation({
    mutationFn: (id: string) => unwrap(api.POST('/api/notifications/{notification_id}/read', { params: { path: { notification_id: id } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.notifications }),
  })
  const readAll = useMutation({
    mutationFn: () => unwrap(api.POST('/api/notifications/read-all', { body: {} })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.notifications }),
  })

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={unread > 0 ? `Уведомления, непрочитанных: ${unread}` : 'Уведомления'}
        className="relative flex h-10 w-10 items-center justify-center rounded-full text-ink hover:bg-panel"
      >
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
          <path
            d="M5.5 15.5h11l-1.4-1.9V9.5a4.1 4.1 0 0 0-8.2 0v4.1L5.5 15.5Zm4 2.2a1.6 1.6 0 0 0 3 0"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {unread > 0 && (
          <span
            aria-hidden="true"
            className="absolute right-0.5 top-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-brand px-1 font-mono text-[11px] font-medium text-white"
          >
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      <Sheet open={open} onClose={() => setOpen(false)} title="Уведомления" description={unread > 0 ? `Непрочитанных: ${unread}` : 'Все прочитано'}>
        <div className="flex flex-col gap-3">
          {q.isPending && <Skeleton className="h-16 w-full rounded-panel" />}
          {q.isError && <Alert tone="danger">{errorMessage(q.error)}</Alert>}
          {q.data && q.data.items.length === 0 && <p className="py-4 text-center t-body text-ink-3">Пока ничего нового.</p>}
          {q.data && q.data.items.length > 0 && (
            <ul className="flex flex-col">
              {q.data.items.map((n) => {
                const to = linkFor(n)
                return (
                  <li key={n.id} className="border-b border-hairline last:border-0">
                    <button
                      type="button"
                      onClick={() => {
                        if (!n.read) readOne.mutate(n.id)
                        if (to) {
                          setOpen(false)
                          navigate(to)
                        }
                      }}
                      className="flex w-full items-start gap-3 py-3 text-left"
                    >
                      <span aria-hidden="true" className={cn('mt-[7px] h-2 w-2 shrink-0 rounded-full', n.read ? 'bg-steel' : 'bg-brand')} />
                      <span className="min-w-0">
                        <span className={cn('block t-body', n.read ? 'text-ink-2' : 'font-medium text-ink')}>{n.message}</span>
                        <span className="block t-small text-ink-3">
                          {n.kind_label} · {n.created_text}
                        </span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {unread > 0 && (
            <Button variant="outline" block loading={readAll.isPending} onClick={() => readAll.mutate()}>
              Отметить всё прочитанным
            </Button>
          )}
        </div>
      </Sheet>
    </>
  )
}
