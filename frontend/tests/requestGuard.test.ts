// Regression tests for the latest-request guard used by the neighbor, trace and
// fixture-concentration queries. Run with `npm test`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequestGuard, runGuarded } from '../src/requestGuard.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

/** Collects which callbacks fired, standing in for React state setters. */
function recorder() {
  const applied: string[] = []
  return { applied, ok: (v: string) => applied.push('ok:' + v), err: (e: unknown) => applied.push('err:' + String(e)) }
}

test('late success after the query time changed is not applied', async () => {
  const guard = createRequestGuard()
  const r = recorder()
  const old = deferred<string>()
  const pending = runGuarded(guard, () => old.promise, r.ok, r.err)
  guard.invalidate() // user switched the query time
  old.resolve('2026-09-07 result')
  await pending
  assert.deepEqual(r.applied, [])
})

test('late failure after the query time changed is not applied', async () => {
  const guard = createRequestGuard()
  const r = recorder()
  const old = deferred<string>()
  const pending = runGuarded(guard, () => old.promise, r.ok, r.err)
  guard.invalidate()
  old.reject('HTTP 404')
  await pending
  assert.deepEqual(r.applied, [])
})

test('after switching time and querying again, only the new result is applied', async () => {
  const guard = createRequestGuard()
  const r = recorder()
  const old = deferred<string>()
  const fresh = deferred<string>()
  const first = runGuarded(guard, () => old.promise, r.ok, r.err)
  guard.invalidate()
  const second = runGuarded(guard, () => fresh.promise, r.ok, r.err)
  fresh.resolve('new')
  await second
  old.resolve('old') // the stale request finishes last
  await first
  assert.deepEqual(r.applied, ['ok:new'])
})

test('the current request still applies both success and failure', async () => {
  const guard = createRequestGuard()
  const r = recorder()
  await runGuarded(guard, async () => 'a', r.ok, r.err)
  await runGuarded(guard, async () => { throw 'boom' }, r.ok, r.err)
  assert.deepEqual(r.applied, ['ok:a', 'err:boom'])
})

test('an older query failing after a newer query succeeded does not replace the result', async () => {
  const guard = createRequestGuard()
  const r = recorder()
  const old = deferred<string>()
  const fresh = deferred<string>()
  const first = runGuarded(guard, () => old.promise, r.ok, r.err)
  const second = runGuarded(guard, () => fresh.promise, r.ok, r.err) // new query, no input change in between
  fresh.resolve('U-00007 @ 09-11')
  await second
  old.reject('HTTP 404')
  await first
  assert.deepEqual(r.applied, ['ok:U-00007 @ 09-11'])
})
