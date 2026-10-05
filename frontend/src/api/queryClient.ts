import { QueryClient } from '@tanstack/react-query'
import { ApiException } from './errors'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // 4xx — ответ окончательный (нет прав, не найдено, 501 «раздел в работе»): повторять бессмысленно
      retry: (failureCount, error) => {
        if (error instanceof ApiException && error.status >= 400 && error.status < 500) return false
        if (error instanceof ApiException && error.status === 501) return false
        return failureCount < 2
      },
    },
    mutations: { retry: false },
  },
})
