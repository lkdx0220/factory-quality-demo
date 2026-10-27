// Latest-request guard shared by pages that query on demand. Free of React and
// of runtime imports so it can be unit-tested with Node's built-in test runner.

/**
 * Latest-request guard: each request takes a token; a response may be applied
 * only while its token is still current. Starting a new request or calling
 * invalidate() (input changed, component unmounted) makes older tokens stale.
 */
export function createRequestGuard() {
  let current = 0
  return {
    begin: () => ++current,
    invalidate: () => { current++ },
    isCurrent: (token: number) => token === current,
  }
}

export type RequestGuard = ReturnType<typeof createRequestGuard>

/** Run `work` under the guard; success and failure callbacks fire only if still current. */
export async function runGuarded<T>(
  guard: RequestGuard,
  work: () => Promise<T>,
  onSuccess: (value: T) => void,
  onError: (error: unknown) => void,
): Promise<void> {
  const token = guard.begin()
  try {
    const value = await work()
    if (guard.isCurrent(token)) onSuccess(value)
  } catch (error) {
    if (guard.isCurrent(token)) onError(error)
  }
}
