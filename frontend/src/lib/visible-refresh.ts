export type VisibleRefreshOptions = {
  /** The minimum delay between refresh attempts while the document is visible. */
  intervalMs?: number;
  /** Refreshes the caller's data using the signal owned by this lifecycle. */
  refresh: (signal: AbortSignal) => void | Promise<void>;
  /** Run once immediately when the lifecycle starts, when the page is visible. */
  immediate?: boolean;
  /** Receives non-abort refresh errors without breaking the lifecycle. */
  onError?: (error: unknown) => void;
};

export type VisibleRefreshHandle = {
  refresh: () => Promise<void>;
  stop: () => void;
};

/**
 * Run a page-local refresh while the document is visible.
 *
 * The helper owns the timer, focus/visibility listeners, request coalescing,
 * and Astro navigation cleanup. Consumers own their endpoint and rendering
 * logic; their callback receives an AbortSignal for no-store fetches.
 */
export function startVisibleRefresh({
  intervalMs = 60_000,
  refresh,
  immediate = false,
  onError,
}: VisibleRefreshOptions): VisibleRefreshHandle {
  const controller = new AbortController();
  let stopped = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  let inFlight: Promise<void> | undefined;

  const run = (): Promise<void> => {
    if (stopped || document.hidden) return Promise.resolve();
    if (inFlight) return inFlight;

    inFlight = Promise.resolve()
      .then(() => refresh(controller.signal))
      .catch((error) => {
        if (!controller.signal.aborted) onError?.(error);
      })
      .finally(() => {
        inFlight = undefined;
      });

    return inFlight;
  };

  const onVisibilityChange = () => {
    if (!document.hidden) void run();
  };
  const onFocus = () => {
    void run();
  };
  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (timer !== undefined) clearInterval(timer);
    timer = undefined;
    controller.abort();
    document.removeEventListener('visibilitychange', onVisibilityChange);
    window.removeEventListener('focus', onFocus);
    document.removeEventListener('astro:before-swap', stop);
  };

  document.addEventListener('visibilitychange', onVisibilityChange);
  window.addEventListener('focus', onFocus);
  document.addEventListener('astro:before-swap', stop, { once: true });
  timer = setInterval(() => void run(), intervalMs);

  if (immediate) void run();

  return { refresh: run, stop };
}
