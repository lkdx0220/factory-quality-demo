// Pure classification helpers for the neighbor-analysis page, kept free of React
// and of runtime imports so they can be unit-tested with Node's built-in test runner.

export interface OperationRef {
  operation_id: string
  lot_id: string
  process_step: string
  track_in_at: string
}

export type OperationKind =
  | { kind: 'original' }
  | { kind: 'repeat'; ordinal: number }
  | { kind: 'unknown' }

/**
 * Ordinal of each operation among operations of the same lot and process step,
 * ordered by track-in time. The data has no explicit rework flag; this ordinal
 * is the only basis for the "original / repeat" split shown on the page.
 */
export function operationOrdinals(ops: OperationRef[]): Map<string, number> {
  const sorted = [...ops].sort((a, b) => a.track_in_at.localeCompare(b.track_in_at) || a.operation_id.localeCompare(b.operation_id))
  const seen = new Map<string, number>()
  const result = new Map<string, number>()
  for (const op of sorted) {
    const key = op.lot_id + '|' + op.process_step
    const n = (seen.get(key) ?? 0) + 1
    seen.set(key, n)
    result.set(op.operation_id, n)
  }
  return result
}

/** An operation missing from the snapshot is "unknown" — never assumed to be original. */
export function classifyOperation(ordinals: Map<string, number>, operationId: string): OperationKind {
  const n = ordinals.get(operationId)
  if (n === undefined) return { kind: 'unknown' }
  return n === 1 ? { kind: 'original' } : { kind: 'repeat', ordinal: n }
}
