// Regression tests for the neighbor page's original/repeat classification.
// Run with `npm test`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { classifyOperation, operationOrdinals, type OperationRef } from '../src/neighborQuery.ts'

// Fixture: the project's synthetic operations table.
const csv = readFileSync(new URL('../../data/batches/demo-v1/business/operations.csv', import.meta.url), 'utf8').trim().split(/\r?\n/)
const header = csv[0].split(',')
const operations = csv.slice(1).map((line) => Object.fromEntries(line.split(',').map((v, i) => [header[i], v]))) as unknown as (OperationRef & { track_out_at: string; recorded_at: string })[]
// Snapshot as it would be listed for a given as_of (completed and recorded by then).
const snapshotAt = (asOf: string) => operations.filter((o) => o.track_out_at <= asOf && o.recorded_at <= asOf)

test('OP-0010 and OP-0013 are repeat operations in the 2026-09-07 snapshot', () => {
  const ordinals = operationOrdinals(snapshotAt('2026-09-07T00:00:00Z'))
  assert.deepEqual(classifyOperation(ordinals, 'OP-0010'), { kind: 'repeat', ordinal: 2 })
  assert.deepEqual(classifyOperation(ordinals, 'OP-0013'), { kind: 'repeat', ordinal: 2 })
  assert.deepEqual(classifyOperation(ordinals, 'OP-0005'), { kind: 'original' })
})

test('operations absent from the snapshot are unknown, never original', () => {
  const ordinals = operationOrdinals(snapshotAt('2026-09-02T00:00:00Z'))
  assert.deepEqual(classifyOperation(ordinals, 'OP-0010'), { kind: 'unknown' })
  assert.deepEqual(classifyOperation(ordinals, 'OP-0013'), { kind: 'unknown' })
})
