import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { Schemas } from '@/api/types'

/**
 * Запросы кабинета кандидата (TanStack Query). Ключи сгруппированы в `qk`, чтобы мутации могли
 * точечно обновлять кэш (`queryClient.invalidateQueries({ queryKey: qk.inbox })`).
 * Все функции — тонкие обёртки над типизированным клиентом `api`.
 */

export type SurveyState = Schemas['SurveyStateOut']
export type SurveyForm = Schemas['SurveyFormOut']
export type Qualification = Schemas['QualificationOut']
export type Briefing = Schemas['BriefingOut']
export type SessionOut = Schemas['SessionOut']
export type ResultOut = Schemas['ResultOut']
export type ReviewOut = Schemas['ReviewOut']
export type ProfileOut = Schemas['ProfileOut']
export type PrivacyOut = Schemas['PrivacyOut']
export type FspStatus = Schemas['FspStatusOut']
export type InboxOut = Schemas['InvitationInboxOut']
export type InvitationDetail = Schemas['InvitationCandidateDetail']
export type TasksOverview = Schemas['TasksOverviewOut']

export const qk = {
  all: ['candidate'] as const,
  surveyForm: ['candidate', 'survey', 'form'] as const,
  survey: ['candidate', 'survey', 'current'] as const,
  surveyHistory: ['candidate', 'survey', 'history'] as const,
  qualifications: ['candidate', 'qualifications'] as const,
  briefing: (spec: string) => ['candidate', 'briefing', spec] as const,
  session: (id: string) => ['candidate', 'session', id] as const,
  result: (id: string) => ['candidate', 'result', id] as const,
  review: (id: string) => ['candidate', 'review', id] as const,
  profile: ['candidate', 'profile'] as const,
  resumeDraft: ['candidate', 'resume-draft'] as const,
  privacy: ['candidate', 'privacy'] as const,
  consents: ['candidate', 'consents'] as const,
  consentDocs: ['candidate', 'consent-docs'] as const,
  fsp: ['candidate', 'fsp'] as const,
  fspAvailability: ['candidate', 'fsp-availability'] as const,
  inbox: ['candidate', 'inbox'] as const,
  invitation: (id: string) => ['candidate', 'invitation', id] as const,
  tasks: ['candidate', 'tasks'] as const,
  vacancies: (onlyMatching: boolean) => ['candidate', 'vacancies', onlyMatching] as const,
  vacancy: (id: string) => ['candidate', 'vacancy', id] as const,
  applications: ['candidate', 'applications'] as const,
  notifications: ['candidate', 'notifications'] as const,
  visibility: ['candidate', 'visibility'] as const,
  pdfInfo: (variant: string) => ['candidate', 'pdf-info', variant] as const,
}

export function useSurveyState() {
  return useQuery({ queryKey: qk.survey, queryFn: () => unwrap(api.GET('/api/survey/current')) })
}

export function useSurveyForm() {
  return useQuery({ queryKey: qk.surveyForm, queryFn: () => unwrap(api.GET('/api/survey/form')), staleTime: 10 * 60_000 })
}

export function useQualifications() {
  return useQuery({ queryKey: qk.qualifications, queryFn: () => unwrap(api.GET('/api/tests/qualification')) })
}

export function useProfile() {
  return useQuery({ queryKey: qk.profile, queryFn: () => unwrap(api.GET('/api/candidates/me')) })
}

export function usePrivacy() {
  return useQuery({ queryKey: qk.privacy, queryFn: () => unwrap(api.GET('/api/candidates/me/privacy')) })
}

export function useFsp() {
  return useQuery({ queryKey: qk.fsp, queryFn: () => unwrap(api.GET('/api/fsp/me')) })
}

export function useInbox() {
  return useQuery({ queryKey: qk.inbox, queryFn: () => unwrap(api.GET('/api/invitations/inbox')) })
}

export function useTasks() {
  return useQuery({ queryKey: qk.tasks, queryFn: () => unwrap(api.GET('/api/tasks/me')) })
}

/** Лучшая подтверждённая квалификация: по баллу; без подтверждённых — первая по списку. */
export function mainQualification(list: readonly Qualification[] | undefined): Qualification | null {
  if (!list || list.length === 0) return null
  const confirmed = list.filter((q) => q.status === 'CONFIRMED' || q.status === 'UPGRADE_TESTING')
  const pool = confirmed.length > 0 ? confirmed : list
  return [...pool].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))[0] ?? null
}

/** Незавершённая сессия теста в квалификации (последняя попытка в открытом статусе). */
export function openAttempt(q: Qualification | null | undefined): { sessionId: string } | null {
  if (!q) return null
  const open = (q.attempts ?? []).find((a) => ['CREATED', 'IN_PROGRESS', 'PAUSED'].includes(a.status))
  return open ? { sessionId: open.session_id } : null
}

export type QueryResult<T> = UseQueryResult<T, Error>
