const DEFAULT_BACKEND_TIMEOUT_MS = 8_000;

/** Fetch with a bounded default deadline while preserving caller cancellation. */
export function fetchWithDeadline(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = DEFAULT_BACKEND_TIMEOUT_MS,
): Promise<Response> {
  return fetch(input, {
    ...init,
    signal: init.signal ?? AbortSignal.timeout(timeoutMs),
  });
}
