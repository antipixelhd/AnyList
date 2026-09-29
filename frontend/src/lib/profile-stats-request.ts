import type { ProfileStats } from "./profile-stats-data";

interface StatsCallbacks {
  render: (data: ProfileStats) => void;
  onError: (message: string) => void;
  onBusy: (busy: boolean) => void;
}

/** Superseded filter requests cannot update the current chart or its feedback. */
export function createProfileStatsLoader(
  username: string,
  callbacks: StatsCallbacks,
) {
  let request: AbortController | undefined;
  let stopped = false;

  return {
    async load(media: ProfileStats["media_type"], year: string) {
      if (stopped) return;
      request?.abort();
      const current = (request = new AbortController());
      callbacks.onBusy(true);
      try {
        const query = new URLSearchParams({ media_type: media });
        if (year) query.set("year", year);
        const response = await fetch(
          `/api/proxy/tracking/profile/${encodeURIComponent(username)}/stats/summary?${query}`,
          {
            signal: current.signal,
            cache: "no-store",
          },
        );
        if (!response.ok) throw new Error("Unable to load these statistics.");
        const data: ProfileStats = await response.json();
        if (!stopped && !current.signal.aborted) callbacks.render(data);
      } catch (cause) {
        if (!stopped && !current.signal.aborted) {
          callbacks.onError(
            cause instanceof Error
              ? cause.message
              : "Unable to load these statistics.",
          );
        }
      } finally {
        if (!stopped && request === current) callbacks.onBusy(false);
      }
    },
    stop() {
      stopped = true;
      request?.abort();
    },
  };
}
