import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { toApiException } from '@/api/errors'
import type { Schemas } from '@/api/types'

// Панель ФСП «Кадровый резерв» (backend/app/api/fsp_dashboard.py): только агрегаты, ячейки меньше k скрыты.

export type Dashboard = Schemas['DashboardOut']
export type DashCell = Schemas['Cell']
export type BreakRow = Schemas['BreakRow']
export type DemandRow = Schemas['DemandRow']

export function useFspDashboard() {
  return useQuery({
    queryKey: ['fsp', 'dashboard'],
    queryFn: () => unwrap(api.GET('/api/fsp/dashboard', {})),
    staleTime: 60_000,
    retry: false,
  })
}

/** Скачивает CSV с теми же агрегатами (скрытые ячейки остаются «<5»). */
export async function downloadFspCsv(): Promise<void> {
  const res = await api.GET('/api/fsp/dashboard/export.csv', { parseAs: 'blob' })
  if (res.error !== undefined || !res.response.ok) throw toApiException(res.response.status, res.error)
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'fsp-reserve.csv'
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
