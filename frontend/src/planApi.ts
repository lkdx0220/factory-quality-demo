import { request, type Operation } from './api'

export interface PlanDraft {
  intent_key: 'lot_operation_inventory'
  original_input: string
  lot_id: string
  as_of: string
}

export interface PlanCard {
  request_id: string
  revision: number
  card_digest: string
  original_input: string
  original_input_label: string
  scope: { lot_id: string; as_of: string; intent_key: string; selection: string; predicate: string; source: string }
  snapshot_digest: string
  contract_bundle_hash: string
  state: 'awaiting_confirmation' | 'incomplete'
  can_confirm: boolean
  answer_published: false
  notice: string
  resolution_receipt: {
    resolution_complete: boolean
    fetched_count: number
    total_count: number | null
    failed_scope: string[]
  }
  coverage_receipt: {
    status: 'covered' | 'not_covered' | 'unknown'
    source: string
    gaps: string[]
    sources: Record<string, { history_from: string; recorded_through: string; history_retained: boolean }>
  }
  member_set: { set_handle: string; set_version: string; digest: string; count: number; preview: Operation[]; exclusions: unknown[] }
  members_url: string
  export_url: string
}

export interface ConfirmedPlan {
  confirmed_plan_id: string
  scope_hash: string
  stage: 'preflight_confirmed'
  answer_published: false
  card: PlanCard
  reused: boolean
  members_url: string
  export_url: string
}

export interface PlanMembers {
  items: Operation[]
  total: number
  offset: number
  next_offset: number | null
  set_handle: string
  set_version: string
  snapshot_digest: string
  draft_revision: number
  resolution_complete: boolean
  requested_scope_count: number
  exclusions: unknown[]
}

export interface EvidenceExecution {
  execution_id: string
  confirmed_plan_id: string
  scope_hash: string
  scope: { lot_id: string; as_of: string }
  state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  execution_complete: boolean
  answer_published: false
  state_persisted: boolean
  cancel_requested?: boolean
  access_state?: 'available' | 'blocked'
  access_error_code?: string
  error_code: string | null
  notice: string
  started_at: string | null
  finished_at: string | null
  completed_scope: string[]
  failed_scope: { source: string; code: string }[]
  not_attempted_scope: string[]
  calls: {
    logical_call_id: string
    source: string
    state: string
    attempts: { attempt: number; attempt_id: string; started_at: string; finished_at: string | null; state: string; error_code: string | null; cleanup_confirmed: boolean | null }[]
  }[]
  evidence: { evidence_id: string; source: string; evidence_digest: string; result: { total_rows: number; returned_rows: number } }[]
  export_url: string
}

const post = <T>(path: string, body: object) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })
// Only server-issued relative links in this feature's namespace are accepted.
function relative(url: string) {
  if (!url.startsWith('/api/v1/query-plans/') || url.includes('..') || url.includes('#')) throw new Error('Invalid preflight link')
  return url.slice('/api/v1'.length)
}

export const planApi = {
  create: (body: PlanDraft) => post<PlanCard>('/query-plans/requests', body),
  confirm: (card: PlanCard) => post<ConfirmedPlan>(`/query-plans/requests/${encodeURIComponent(card.request_id)}/confirm`, {
    revision: card.revision, card_digest: card.card_digest,
  }),
  members: (url: string, offset = 0) => request<PlanMembers>(relative(url) + `&offset=${offset}&limit=10`),
  export: (url: string) => request<Record<string, unknown>>(relative(url)),
  execute: (plan: ConfirmedPlan) => post<EvidenceExecution>(`/query-plans/confirmed/${encodeURIComponent(plan.confirmed_plan_id)}/execute`, { scope_hash: plan.scope_hash }),
  execution: (id: string) => request<EvidenceExecution>(`/query-plans/executions/${encodeURIComponent(id)}`),
  cancelExecution: (id: string) => post<EvidenceExecution>(`/query-plans/executions/${encodeURIComponent(id)}/cancel`, {}),
  executionExport: (url: string) => request<Record<string, unknown> & { execution: EvidenceExecution }>(relative(url)),
}
