import { request } from './api'
import type { PlanCard } from './planApi'

export interface PlannerConfig {
  enabled: boolean
  provider: string
  model: string
  prompt_version: string
  notice: string
}

export interface ModelProposal {
  proposal_id: string
  proposal_digest: string
  original_input: string
  original_input_label: string
  status: 'ready' | 'needs_clarification' | 'unsupported'
  reason_codes: string[]
  questions: string[]
  notice: string
  normalized_scope: { intent_key: 'lot_operation_inventory'; lot_id: string; as_of: string } | null
  model_called: boolean
  model_call: null | { model: string; prompt_version: string; call_id: string; returned_model: string; usage: { total_tokens: number } }
  answer_published: false
}

export const plannerApi = {
  config: () => request<PlannerConfig>('/query-plans/nl/config'),
  propose: (question: string) => request<ModelProposal>('/query-plans/nl/proposals', {
    method: 'POST', body: JSON.stringify({ question }),
  }),
  preflight: (proposal: ModelProposal) => request<PlanCard>(`/query-plans/nl/proposals/${encodeURIComponent(proposal.proposal_id)}/preflight`, {
    method: 'POST', body: JSON.stringify({ proposal_digest: proposal.proposal_digest }),
  }),
}
