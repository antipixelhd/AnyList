/** Refresh every cached title while keeping each request within the API limit. */
export async function refreshBrowseContextBatches<T, R>(
  items: T[],
  fetchBatch: (batch: T[]) => Promise<R[]>,
  applyBatch: (rows: R[]) => void,
  isCurrent: () => boolean,
) {
  for (let offset = 0; offset < items.length && isCurrent(); offset += 80) {
    try {
      const rows = await fetchBatch(items.slice(offset, offset + 80));
      if (!isCurrent()) return;
      applyBatch(rows);
    } catch {
      // Keep cached results usable and allow other batches to refresh.
    }
  }
}
