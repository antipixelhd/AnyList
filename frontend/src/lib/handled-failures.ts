// Network failures can be reported by either the initiating control or the global fallback.
const handled = new WeakSet<object>();
export function markFailureHandled(error: unknown) {
  if (error && (typeof error === 'object' || typeof error === 'function')) handled.add(error as object);
}
export function isFailureHandled(error: unknown) {
  return !!error && (typeof error === 'object' || typeof error === 'function') && handled.has(error as object);
}
