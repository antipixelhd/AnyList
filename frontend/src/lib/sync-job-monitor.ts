import type { SyncJob } from "./api";

interface JobWatch {
  matches: (job: SyncJob) => boolean;
  render: (job: SyncJob | null) => void;
  onUpdate?: (active: boolean) => void;
  onDone?: () => void;
  onError?: () => void;
  jobId?: number;
  recentMs?: number;
  hideMs?: number;
}

interface WatchState {
  options: JobWatch;
  trackedId?: number;
  expiresAt?: number;
}

export function isActiveSyncJob(job: SyncJob) {
  return ["pending", "running", "in_progress"].includes(job.status);
}

function updatedAt(job: SyncJob) {
  const value = job.updated_at;
  return Date.parse(
    /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`,
  );
}

/** Share a status request across all page widgets and own its navigation lifetime. */
export function createSyncJobMonitor(token: string) {
  const controller = new AbortController();
  const watches = new Map<string, WatchState>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<void> | undefined;

  function clearTimer() {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  }

  function hideExpired() {
    for (const [key, state] of watches) {
      if (state.expiresAt !== undefined && state.expiresAt <= Date.now()) {
        state.options.render(null);
        watches.delete(key);
      }
    }
  }

  function schedule(retryMs = 2000) {
    clearTimer();
    if (controller.signal.aborted || !watches.size) return;
    const delays = [...watches.values()].map((state) =>
      state.expiresAt === undefined
        ? retryMs
        : Math.max(0, state.expiresAt - Date.now()),
    );
    timer = setTimeout(
      () => {
        timer = undefined;
        hideExpired();
        if (
          [...watches.values()].some((state) => state.expiresAt === undefined)
        )
          return refresh();
        else schedule();
      },
      Math.min(...delays),
    );
  }

  function apply(jobs: SyncJob[]) {
    hideExpired();
    for (const [key, state] of watches) {
      if (state.expiresAt !== undefined) continue;
      const { options } = state;
      const matching = jobs.filter(
        (job) =>
          options.matches(job) &&
          (options.jobId === undefined || job.id === options.jobId),
      );
      const job =
        matching.find(isActiveSyncJob) ||
        matching.find((job) => job.id === state.trackedId) ||
        matching[0];
      // A newly queued or previously running job missing from this snapshot
      // does not establish completion. Keep its progress/lock and retry.
      if (
        !job &&
        (options.jobId !== undefined || state.trackedId !== undefined)
      )
        continue;
      const active = !!job && isActiveSyncJob(job);
      options.onUpdate?.(active);
      if (active) {
        state.trackedId = job.id;
        options.render(job);
      } else if (
        job &&
        ["completed", "failed", "cancelled"].includes(job.status) &&
        (job.id === state.trackedId ||
          options.jobId === job.id ||
          updatedAt(job) > Date.now() - (options.recentMs ?? 8000))
      ) {
        options.render(job);
        state.expiresAt = Date.now() + (options.hideMs ?? 4000);
        options.onDone?.();
      } else {
        options.render(null);
        watches.delete(key);
      }
    }
  }

  function refresh(): Promise<void> {
    if (controller.signal.aborted) return Promise.resolve();
    if (inFlight) return inFlight;
    clearTimer();
    inFlight = Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      let delay = 2000;
      try {
        const response = await fetch("/api/proxy/sync/status", {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok)
          throw new Error(`Sync status unavailable (${response.status})`);
        const jobs: SyncJob[] = await response.json();
        if (!controller.signal.aborted) apply(jobs);
      } catch {
        if (!controller.signal.aborted) {
          // Keep running-job locks and progress intact until a successful status response.
          for (const state of watches.values()) state.options.onError?.();
          delay = 5000;
        }
      } finally {
        inFlight = undefined;
        schedule(delay);
      }
    });
    return inFlight;
  }

  function wake() {
    if (
      !document.hidden &&
      [...watches.values()].some((state) => state.expiresAt === undefined)
    )
      void refresh();
  }

  function stop() {
    clearTimer();
    controller.abort();
    watches.clear();
    document.removeEventListener("visibilitychange", wake);
    document.removeEventListener("astro:before-swap", stop);
  }

  document.addEventListener("visibilitychange", wake);
  document.addEventListener("astro:before-swap", stop, { once: true });
  return {
    watch(key: string, options: JobWatch) {
      if (controller.signal.aborted) return Promise.resolve();
      watches.set(key, { options });
      return refresh();
    },
    stop,
  };
}
