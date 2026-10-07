import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { Schemas } from '@/api/types'

export type ChatThread = Schemas['ChatThreadOut']
export type ChatKind = 'invitation' | 'application'

const chatKey = (kind: ChatKind, id: string) => ['chat', kind, id] as const

/** Переписка по приглашению или отклику; опрос раз в 8 секунд, пока вкладка открыта. */
export function useChatThread(kind: ChatKind, id: string) {
  return useQuery({
    queryKey: chatKey(kind, id),
    queryFn: () => unwrap(api.GET('/api/chat/{kind}/{ref_id}', { params: { path: { kind, ref_id: id } } })),
    refetchInterval: 8000,
    retry: false,
  })
}

/** Счётчик непрочитанных сообщений пользователя (для значков в навигации). */
export function useChatUnread() {
  return useQuery({
    queryKey: ['chat', 'unread'],
    queryFn: () => unwrap(api.GET('/api/chat/unread')),
    refetchInterval: 30000,
    select: (d) => d.unread_total,
  })
}

export function useSend(kind: ChatKind, id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: string) =>
      unwrap(api.POST('/api/chat/{kind}/{ref_id}/messages', { params: { path: { kind, ref_id: id } }, body: { body } })),
    onSuccess: (data) => {
      qc.setQueryData(chatKey(kind, id), data)
      void qc.invalidateQueries({ queryKey: ['chat', 'unread'] })
    },
  })
}
