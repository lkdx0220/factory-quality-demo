import { request } from './api'

export interface DisplayFact {
  key: string; label: string; fact_id: string; value: string | number | boolean | null
  display_value: string; unit: string; origin: 'registered_derivation' | 'source_value' | 'confirmed_scope'
}
export interface DisplayOperation { operation_id: string; cells: DisplayFact[] }
export interface PublicationView {
  publication_id: string; publication_digest: string; status: 'verified_answer'; answer_published: true
  title: string; status_label: string; notice: string; source_notice: string; time_notice: string; order_notice: string
  published_at: string; expires_at_epoch: number; summary: DisplayFact[]; preview: DisplayOperation[]
  preview_rows: number; total_rows: number; details_url: string; export_url: string; model_output_used: boolean
  completeness: { required_facts: number; rendered_facts: number }
}
export interface PublicationPage {
  publication_id: string; publication_digest: string; items: DisplayOperation[]
  total: number; returned_rows: number; offset: number; next_offset: number | null
}
export type PublicationArtifact = Record<string, unknown> & { view: PublicationView }
function relative(url: string) {
  if (!url.startsWith('/api/v1/query-plans/publications/') || url.includes('..') || url.includes('#')) throw new Error('Invalid publication link')
  return url.slice('/api/v1'.length)
}
export const publicationApi = {
  publish: (id: string, digest: string, validationId: string) => request<PublicationView>(
    `/query-plans/fact-sets/${encodeURIComponent(id)}/publish`, {
      method: 'POST', body: JSON.stringify({ fact_set_digest: digest, validation_id: validationId }),
    }),
  read: (id: string) => request<PublicationView>(`/query-plans/publications/${encodeURIComponent(id)}`),
  members: (url: string, offset = 0) => request<PublicationPage>(relative(url) + `&offset=${offset}&limit=3`),
  export: (url: string) => request<PublicationArtifact>(relative(url)),
}
