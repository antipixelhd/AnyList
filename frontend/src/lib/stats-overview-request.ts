import type { MediaScope, OverviewResponse } from './stats-overview-data';

interface Callbacks {
  render: (data: OverviewResponse) => void;
  onError: (message: string, denied: boolean) => void;
  onBusy: (busy: boolean) => void;
}

export function createOverviewLoader(username: string, callbacks: Callbacks) {
  let active: AbortController | undefined;
  let stopped = false;
  return {
    async load(media: MediaScope) {
      if (stopped) return;
      active?.abort();
      const current = active = new AbortController();
      callbacks.onBusy(true);
      try {
        const response = await fetch(`/api/proxy/tracking/profile/${encodeURIComponent(username)}/stats/overview?media_type=${media}`, { signal: current.signal, cache: 'no-store' });
        if (response.redirected) {
          if (!stopped && !current.signal.aborted) callbacks.onError('Sign in to view these statistics.', true);
          return;
        }
        if (!response.ok) {
          if (!stopped && !current.signal.aborted) callbacks.onError(response.status === 403 ? 'This profile is private.' : 'Unable to load these statistics.', [401, 403, 404].includes(response.status));
          return;
        }
        const data: OverviewResponse = await response.json();
        if (!stopped && !current.signal.aborted) callbacks.render(data);
      } catch {
        if (!stopped && !current.signal.aborted) callbacks.onError('Unable to load these statistics. Try again.', false);
      } finally {
        if (!stopped && active === current) callbacks.onBusy(false);
      }
    },
    stop() { stopped = true; active?.abort(); },
  };
}
