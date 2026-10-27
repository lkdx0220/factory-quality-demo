// Thin client over the existing FastAPI contract (docs/api.md).
// All calculation results come from the backend; this module only transports
// them and turns failures into a uniform, displayable error.

export type Decision = 'ALLOW' | 'HOLD' | 'REVIEW_BLOCKED'
export type Outcome = 'PASS' | 'FAIL' | 'INVALID'

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

export interface Health { status: string; version: string; database_exists: boolean }

export interface Stats {
  source: { batch_id: string; data_digest: string; manifest_digest: string; imported_at: string }
  test_record_versions: number
  test_attempts: number
  delivery_receipts: number
  duplicate_deliveries: number
  packaging_decisions: number
  lots: number
  units: number
}

export interface IngestResult {
  batch_id: string
  data_digest: string
  reused: boolean
  test_record_versions: number
  test_attempts: number
  delivery_receipts: number
  duplicate_deliveries: number
  packaging_decisions: number
}

export interface Lot { lot_id: string; product_id: string; initial_quantity: number; created_at: string }
export interface Unit { unit_id: string; lot_id: string; created_at: string }
export interface Equipment { equipment_id: string; equipment_name: string }
export interface Operation {
  operation_id: string
  lot_id: string
  process_step: string
  equipment_id: string
  track_in_at: string
  track_out_at: string | null
  recorded_at: string
}
export interface Check { check_id: string; unit_id: string; requested_sn: string; check_at: string }

export interface DecisionTest {
  test_type: string
  requirement_id: string | null
  required: boolean | null
  status: string
  expired?: boolean
  test_id?: string | null
  test_record_id?: string | null
  evidence_test_record_ids?: string[]
  attempt_no?: number | null
  version?: number | null
  completed_at?: string | null
  age_seconds?: number | null
  max_age_seconds?: number | null
}

export interface DecisionResult {
  check_id: string
  decision: Decision
  reason_codes: string[]
  identity: {
    unit_found: boolean
    lot_id: string
    product_id: string
    sn_status: 'OK' | 'MISSING' | 'CONFLICT' | 'MISMATCH'
    assignment_ids: string[]
    visible_sns: string[]
  }
  tests: DecisionTest[]
}

export interface Preview extends DecisionResult { persisted: false }

export interface EvaluateResult extends DecisionResult {
  decision_event: { decision_id: string; policy_version: string; reused: boolean }
  mes_write_performed: boolean
}

export interface SavedDecision {
  decision_id: string
  check_id: string
  policy_version: string
  decision: Decision
  evaluated_at: string
  result: DecisionResult
}

export interface TraceOperation {
  operation_id: string
  process_step: string
  equipment_id: string
  track_in_at: string
  track_out_at: string | null
  status: 'completed' | 'in_progress'
}

export interface TraceTest {
  test_type: string
  attempt_no: number
  test_id: string
  version: number
  test_record_id: string
  operation_id: string
  equipment_id: string
  binding_id: string | null
  started_at: string
  completed_at: string
  outcome: Outcome
  recorded_at: string
  binding?: { fixture_id: string; equipment_id: string; position_id: string; valid_from: string; valid_to: string | null } | null
}

export interface TraceAlarm {
  alarm_id: string
  scope: string
  alarm_code: string
  started_at: string
  ended_at: string | null
  recorded_at: string
  equipment_id?: string
  context_only?: boolean
  overlaps_unit_activity?: boolean
}

export interface UnitTrace {
  unit_id: string
  as_of: string
  lot_id: string
  product_id: string
  operations: TraceOperation[]
  operation_ids: string[]
  visible_sn: { sn: string; assignment_id: string; assigned_at: string; recorded_at: string }[]
  distinct_test_count: number
  tests: TraceTest[]
  unit_linked_alarms: TraceAlarm[]
  equipment_context_alarms: TraceAlarm[]
  delivery_counts: Record<string, number>
}

export interface LotTrace { lot_id: string; as_of: string; unit_count: number; units: UnitTrace[] }

export interface OperationStats {
  operation_id: string
  lot_id: string
  product_id: string
  track_out_at: string
  test_type: string
  denominator: number
  first_pass_count: number
  final_pass_count: number
  fail_count: number
  invalid_count: number
  missing_count: number
  first_pass_yield: number | null
  final_yield: number | null
  tested_count: number
  coverage: number | null
}

export interface Neighbors {
  anchor_operation_id: string
  equipment_id: string
  process_step: string
  as_of: string
  n_before: number
  n_after: number
  same_product_filter: boolean
  anchor_stats: OperationStats
  before: OperationStats[]
  after: OperationStats[]
  before_count: number
  after_count: number
  before_insufficient: boolean
  after_insufficient: boolean
}

export interface Concentration {
  analysis: string
  equipment_id: string
  window: { from: string; to: string }
  groups: { binding_id: string | null; fixture_id: string | null; position_id: string | null; attempts: number; fail_attempts: number; fail_rate: number | null }[]
  interpretation: string
}

/** A failure the UI can show verbatim: backend error envelope or transport problem. */
export class ApiError extends Error {
  constructor(
    public kind: 'backend' | 'unreachable',
    public status: number | null,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message)
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch('/api/v1' + path, {
      ...init,
      headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}) },
    })
  } catch (exc) {
    throw new ApiError('unreachable', null, 'NETWORK_ERROR', String(exc))
  }
  const text = await response.text()
  let body: unknown
  try {
    body = text ? JSON.parse(text) : undefined
  } catch {
    body = undefined
  }
  const envelope = body as { ok?: boolean; data?: T; error?: { code: string; message: string; details?: unknown } } | undefined
  if (envelope && envelope.ok === true) return envelope.data as T
  if (envelope && envelope.ok === false && envelope.error) {
    throw new ApiError('backend', response.status, envelope.error.code, envelope.error.message, envelope.error.details)
  }
  // Non-envelope responses mean the dev proxy could not reach the API process.
  throw new ApiError('unreachable', response.status, 'BACKEND_UNREACHABLE',
    `HTTP ${response.status}${text ? ': ' + text.slice(0, 200) : '（无响应内容）'}`)
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? '?' + text : ''
}

const post = <T>(path: string, body: object = {}) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })

/** Read every page of a list endpoint (the demo dataset is small). */
async function all<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T[]> {
  const items: T[] = []
  let offset: number | null = 0
  while (offset !== null) {
    const page: Page<T> = await request<Page<T>>(path + query({ ...params, limit: 100, offset }))
    items.push(...page.items)
    offset = page.next_offset
  }
  return items
}

export const api = {
  health: () => request<Health>('/health'),
  stats: () => request<Stats>('/stats'),
  ingest: () => post<IngestResult>('/ingest'),
  lots: () => all<Lot>('/lots'),
  units: (lot_id?: string) => all<Unit>('/units', { lot_id }),
  equipment: () => all<Equipment>('/equipment'),
  operations: (params: { lot_id?: string; equipment_id?: string; as_of?: string } = {}) => all<Operation>('/operations', params),
  checks: (unit_id?: string) => all<Check>('/checks', { unit_id }),
  decisions: (check_id?: string) => all<SavedDecision>('/decisions', { check_id }),
  preview: (checkId: string) => request<Preview>(`/checks/${encodeURIComponent(checkId)}/preview`),
  evaluate: (checkId: string) => post<EvaluateResult>(`/checks/${encodeURIComponent(checkId)}/evaluate`),
  trace: (as_of: string, identity: { unit_id?: string; sn?: string; lot_id?: string }) =>
    request<UnitTrace | LotTrace>('/trace' + query({ as_of, ...identity })),
  neighbors: (operationId: string, params: { as_of: string; before: number; after: number; same_product: boolean }) =>
    request<Neighbors>(`/operations/${encodeURIComponent(operationId)}/neighbors` + query(params)),
  concentration: (params: { equipment_id: string; start: string; end: string }) =>
    request<Concentration>('/concentration' + query(params)),
}
