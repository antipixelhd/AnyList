export interface StatisticsRefresh {
  status: 'pending' | 'ready' | 'refreshing' | 'error';
  generation: number | null;
  computed_at: string | null;
}

export async function refreshStatistics(
  signal: AbortSignal,
  request: typeof fetch = fetch,
  pause = (ms: number) => new Promise<void>((resolve, reject) => {
    const stop = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', stop); resolve(); }, ms);
    if (signal.aborted) stop();
    else signal.addEventListener('abort', stop, { once: true });
  }),
): Promise<StatisticsRefresh> {
  const deadline = Date.now() + 130_000;
  let method = 'POST';
  while (true) {
    signal.throwIfAborted();
    const response = await request('/api/proxy/tracking/stats/refresh', { method, cache: 'no-store', signal });
    if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? 'Sign in again to refresh statistics.' : 'Could not refresh statistics. Try again.');
    const result: StatisticsRefresh = await response.json();
    if (result.status === 'ready' && result.generation !== null && result.computed_at) return result;
    if (result.status === 'pending') throw new Error('Your data changed during the refresh. Try again.');
    if (result.status !== 'refreshing') throw new Error('Could not refresh statistics. Try again.');
    if (Date.now() >= deadline) throw new Error('The refresh is still running. Check your statistics shortly.');
    await pause(1000);
    method = 'GET';
  }
}
