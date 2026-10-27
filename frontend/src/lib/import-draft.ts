type Session = {id: string; revision: number};
export type ImportPatch = {itemId: string | number; changes: Record<string, unknown>};

/** Keep review choices local until a step is submitted. Server snapshots are
 * always rebased over newer choices, including after a failed save is retried. */
export function createImportDraft<S extends Session>(options: {
  getSession: () => S | null;
  setSession: (session: S) => void;
  apply: (session: S, patch: ImportPatch) => void;
  save: (session: S, patch: ImportPatch) => Promise<S>;
  saveBatch?: (session: S, patches: ImportPatch[]) => Promise<S>;
}) {
  let sessionId: string | undefined;
  let pending: ImportPatch[] = [];
  let flushing: Promise<void> | undefined;
  let inFlight = new Set<ImportPatch>();
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
    stage(patch: ImportPatch) {
      const current = sync();
      if (!current) return;
      const previous = pending.find(value => !inFlight.has(value) && String(value.itemId) === String(patch.itemId));
      if (previous) Object.assign(previous.changes, patch.changes);
      else pending.push({itemId: patch.itemId, changes: {...patch.changes}});
      options.apply(current, patch);
    },
    rebase,
    snapshot() { sync(); return structuredClone(pending); },
    restore(patches: ImportPatch[]) {
      sync();
      pending = structuredClone(patches);
      const current = options.getSession();
      if (current) rebase(current);
    },
    flush(): Promise<void> {
      if (flushing) return flushing;
      const initial = sync();
      if (!initial) return Promise.resolve();
      const id = initial.id;
      flushing = (async () => {
        while (pending.length && options.getSession()?.id === id) {
          const batch = options.saveBatch ? [...pending] : [pending[0]];
          inFlight = new Set(batch);
          const fresh = options.saveBatch
            ? await options.saveBatch(options.getSession()!, structuredClone(batch))
            : await options.save(options.getSession()!, batch[0]);
          if (options.getSession()?.id !== id) return;
          pending = pending.filter(patch => !inFlight.has(patch));
          rebase(fresh);
        }
      })().finally(() => {flushing = undefined; inFlight.clear();});
      return flushing;
    },
  };
}
