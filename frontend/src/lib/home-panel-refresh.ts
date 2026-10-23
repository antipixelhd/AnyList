type Options = {
  signal: AbortSignal;
  isBusy: () => boolean;
  replace: (fresh: Document) => void;
};

/** Reconcile structural home changes without navigating or disturbing the app shell. */
export function createHomePanelRefresh({signal, isBusy, replace}: Options) {
  let pending: Promise<void> | undefined;
  return () => {
    if (signal.aborted || isBusy()) return Promise.resolve();
    if (pending) return pending;
    pending = (async () => {
      const response = await fetch('/home', {cache:'no-store', signal});
      if (!response.ok || signal.aborted) return;
      const html = await response.text();
      if (signal.aborted || isBusy()) return;
      const fresh = new DOMParser().parseFromString(html, 'text/html');
      // Parsed scripts are never executed; only the server-rendered sidebar is adopted.
      replace(fresh);
    })().catch(error => {
      if (!signal.aborted) console.debug('Home panel refresh deferred.', error);
    }).finally(() => { pending = undefined; });
    return pending;
  };
}
