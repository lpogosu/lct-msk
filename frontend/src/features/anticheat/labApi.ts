import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { Schemas } from '@/api/types'

// Запросы существующих экранов лаборатории: сессии и трасса, банк заданий и «3 клона рядом».

export type LabSession = Schemas['LabSessionOut']
export type Trace = Schemas['TraceOut']
export type TraceStep = Schemas['TraceStepOut']
export type Bank = Schemas['app__services__testing__schemas__BankOut']
export type BankFamily = Schemas['BankFamilyOut']
export type Clones = Schemas['ClonesOut']

/** Подписи статусов сессии (app.testing.session.STATUS_LABELS_RU). */
export const SESSION_STATUS_RU: Record<string, string> = {
  CREATED: 'ещё не начат',
  IN_PROGRESS: 'идёт',
  PAUSED: 'на паузе',
  ABANDONED: 'прерван',
  DECIDED: 'решён',
  EXTENSION_OFFERED: 'ждёт решения о доборе',
  FINAL: 'закрыт',
  UNDER_REVIEW: 'на проверке у методиста',
  INVALIDATED: 'аннулирован',
}

export const GRADE_RU: Record<string, string> = {
  intern: 'Стажёр',
  junior: 'Junior',
  middle: 'Middle',
  senior: 'Senior',
  below_intern: 'Ниже Стажёра',
}

export const labKeys = {
  sessions: (status: string, spec: string) => ['lab', 'sessions', status, spec] as const,
  trace: (id: string) => ['lab', 'trace', id] as const,
  bank: (spec: string) => ['lab', 'bank', spec] as const,
  clones: (family: string, n: number) => ['lab', 'clones', family, n] as const,
}

export function useLabSessions(status: string, specialization: string) {
  return useQuery({
    queryKey: labKeys.sessions(status, specialization),
    queryFn: () =>
      unwrap(
        api.GET('/api/lab/sessions', {
          params: { query: { ...(status ? { status } : {}), ...(specialization ? { specialization } : {}), limit: 100 } },
        }),
      ),
  })
}

export function useTrace(sessionId: string) {
  return useQuery({
    queryKey: labKeys.trace(sessionId),
    queryFn: () => unwrap(api.GET('/api/lab/sessions/{session_id}/trace', { params: { path: { session_id: sessionId } } })),
    retry: false,
  })
}

export function useLabBank(specialization: string) {
  return useQuery({
    queryKey: labKeys.bank(specialization),
    queryFn: () => unwrap(api.GET('/api/lab/bank', { params: { query: specialization ? { specialization } : {} } })),
  })
}

export function useClones(familyId: string, n = 3) {
  return useQuery({
    queryKey: labKeys.clones(familyId, n),
    queryFn: () =>
      unwrap(api.GET('/api/lab/bank/{family_id}/clones', { params: { path: { family_id: familyId }, query: { n } } })),
    retry: false,
  })
}
