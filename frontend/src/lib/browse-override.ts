/** Deliberately memory-only: reloads restore the account's curated policy. */
export function createBrowseOverride(initialQuery = '') {
  let query = initialQuery.trim(), active = false;
  return {
    forQuery(value: string) {
      const next = value.trim();
      if (next !== query) { query = next; active = false; }
      return active;
    },
    set(value: boolean) { active = value; },
  };
}
