import { request } from './api'

export interface RegisteredFact {
  fact_id: string
  fact_key: string
  claim_type: string
  entity_id: string
  operation_id: string | null
  as_of: string
  metric_key: string
  metric_version: string
  rule_version: string
  unit: string
  denominator_domain: string
  value_type: 'string' | 'integer' | 'boolean' | 'null'
  value: string | number | boolean | null
  algorithm_id: string | null
  input_fact_ids: string[]
  source_leaf: { evidence_id: string; source: string; record_id: string; field: string } | null
}

export interface FactValidation {
  validation_id: string
  fact_set_id: string
  fact_set_digest: string
  accepted: boolean
  g2_passed: boolean
  g3_passed: boolean
  required_count: number
  submitted_count: number
  missing_required_fact_ids: string[]
  issues: { code: string; index: number | null; fields: string[] }[]
  answer_published: false
}

export interface FactView {
  fact_set_id: string
  fact_set_digest: string
  execution_id: string
  lot_id: string
  as_of: string
  registry_version: string
  contract_bundle_hash: string
  evidence_set_digest: string
  summary_facts: RegisteredFact[]
  fact_count: number
  required_count: number
  required_fact_ids: string[]
  g2_passed: boolean
  answer_published: false
  notice: string
  completeness: { execution: string; business_evidence: string; business_reason: string; presentation: string }
  members_url: string
  export_url: string
  latest_validation: FactValidation | null
}

export interface FactPage {
  fact_set_id: string
  fact_set_digest: string
  items: RegisteredFact[]
  total: number
  returned_rows: number
  offset: number
  next_offset: number | null
}

type FactArtifact = Record<string, unknown> & { view: FactView }
function relative(url: string) {
  if (!url.startsWith('/api/v1/query-plans/fact-sets/') || url.includes('..') || url.includes('#')) throw new Error('Invalid fact link')
  return url.slice('/api/v1'.length)
}
const root = (id: string) => `/query-plans/fact-sets/${encodeURIComponent(id)}`
const post = <T,>(path: string, body: object) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })
export const factsApi = {
  create: (executionId: string) => post<FactView>(`/query-plans/executions/${encodeURIComponent(executionId)}/facts`, {}),
  read: (id: string) => request<FactView>(root(id)),
  members: (url: string, offset = 0) => request<FactPage>(relative(url) + `&offset=${offset}&limit=10`),
  proposal: (id: string) => request<Record<string, unknown>>(root(id) + '/proposal'),
  validate: (id: string, proposal: Record<string, unknown>) => post<FactValidation>(root(id) + '/validate', proposal),
  export: (url: string) => request<FactArtifact>(relative(url)),
}
