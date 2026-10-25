type Session = {id: string; revision: number};
type Patch = {itemId: string | number; changes: Record<string, unknown>};

/** Keep review choices local until a step is submitted. Server snapshots are
 * always rebased over newer choices, including after a failed save is retried. */
export function createImportDraft<S extends Session>(options: {
  getSession: () => S | null;
  setSession: (session: S) => void;
  apply: (session: S, patch: Patch) => void;
  save: (session: S, patch: Patch) => Promise<S>;
}) {
  let sessionId: string | undefined;
  let pending: Patch[] = [];
  let flushing: Promise<void> | undefined;
  let inFlight: Patch | undefined;
  function sync() {
    const current = options.getSession();
    if (current?.id !== sessionId) {
      sessionId = current?.id;
      pending = [];
    }
    return current;
  }
  function rebase(fresh: S) {
    if (options.getSession()?.id !== fresh.id) return;
    for (const patch of pending) options.apply(fresh, patch);
    options.setSession(fresh);
  }
  return {
    stage(patch: Patch) {
      const current = sync();
      if (!current) return;
      const previous = pending.find(value => value !== inFlight && String(value.itemId) === String(patch.itemId));
      if (previous) Object.assign(previous.changes, patch.changes);
      else pending.push({itemId: patch.itemId, changes: {...patch.changes}});
      options.apply(current, patch);
    },
    rebase,
    flush(): Promise<void> {
      if (flushing) return flushing;
      const initial = sync();
      if (!initial) return Promise.resolve();
      const id = initial.id;
      flushing = (async () => {
        while (pending.length && options.getSession()?.id === id) {
          const patch = pending[0];
          inFlight = patch;
          const fresh = await options.save(options.getSession()!, patch);
          if (options.getSession()?.id !== id) return;
          pending.shift();
          rebase(fresh);
        }
      })().finally(() => {flushing = undefined; inFlight = undefined;});
      return flushing;
    },
  };
}
