type Options = {
  signal: AbortSignal;
  isBusy: () => boolean;
  getRevision?: () => number;
  replace: (fresh: Document) => void;
};

/** Reconcile structural home changes without navigating or disturbing the app shell. */
export function createHomePanelRefresh({signal, isBusy, getRevision=()=>0, replace}: Options) {
  let pending: Promise<void> | undefined;
  return (onFailure?: () => void) => {
    if (signal.aborted || isBusy()) return Promise.resolve();
    if (pending) return pending.catch(error => { if (!signal.aborted) { if (onFailure) onFailure(); else console.debug('Home panel refresh deferred.', error); } });
    const revision = getRevision();
    pending = (async () => {
      const response = await fetch('/home', {cache:'no-store', signal});
      if (!response.ok) throw new Error('Home panel refresh failed.');
      if (signal.aborted) return;
      const html = await response.text();
      if (signal.aborted || isBusy() || revision !== getRevision()) return;
      const fresh = new DOMParser().parseFromString(html, 'text/html');
      // Parsed scripts are never executed; only the server-rendered sidebar is adopted.
      replace(fresh);
    })().finally(() => { pending = undefined; });
    return pending.catch(error => { if (!signal.aborted) { if (onFailure) onFailure(); else console.debug('Home panel refresh deferred.', error); } });
  };
}
