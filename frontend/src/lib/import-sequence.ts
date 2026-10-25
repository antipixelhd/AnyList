/** Review navigation is separate from the optimistic import decisions. */
export function createImportSequence() {
  let ids: string[] = [];
  const known = new Set<string>();
  const finished = new Set<string>();
  let selected: string | null = null;
  let overview = false;
  return {
    get ids() { return [...ids]; },
    get selected() { return selected; },
    get overview() { return overview; },
    sync(values: Array<string | number>) {
      for (const value of values) {
        const id = String(value);
        if (!known.has(id)) { known.add(id); ids.push(id); }
      }
    },
    reset() { ids = []; known.clear(); finished.clear(); selected = null; overview = false; },
    pending() { return ids.filter(id => !finished.has(id)); },
    current(pending = ids.filter(id => !finished.has(id))) { return selected ?? (overview ? null : pending[0] ?? null); },
    visit(value: string | number) { selected = String(value); },
    complete(value: string | number) { finished.add(String(value)); selected = null; },
    invalidate(value: string | number) { finished.delete(String(value)); if (selected === String(value)) selected = null; },
    resume() { selected = null; },
    showOverview() { overview = true; selected = null; },
    start() { overview = false; selected = null; },
  };
}
