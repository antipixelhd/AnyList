import type { ImportPatch } from './import-draft';

type Sequence = {ids: string[]; finished: string[]; selected: string | null; overview: boolean};
export type ImportCheckpoint = {
  revision: number; expiresAt: string; step: number; patches: ImportPatch[];
  matches: Sequence; progress: Sequence;
};
const prefix = 'anylist:import:';
/** Store only private review choices, scoped to the signed-in viewer and draft. */
export function importRecovery(storage: Storage, viewer: string) {
  const key = (id: string) => `${prefix}${encodeURIComponent(viewer)}:${id}`;
  return {
    save(id: string, value: ImportCheckpoint) {
      try { storage.setItem(key(id), JSON.stringify(value)); } catch { /* Private mode/quota must never block review. */ }
    },
    read(id: string): ImportCheckpoint | null {
      try {
        const value = JSON.parse(storage.getItem(key(id)) || 'null');
        if (value && Date.parse(value.expiresAt) > Date.now() && Number.isInteger(value.step)
            && value.step >= 1 && value.step <= 5 && Array.isArray(value.patches)
            && Array.isArray(value.matches?.ids) && Array.isArray(value.matches?.finished)
            && Array.isArray(value.progress?.ids) && Array.isArray(value.progress?.finished)) return value;
        storage.removeItem(key(id));
      } catch { /* Treat unavailable or malformed storage as no checkpoint. */ }
      return null;
    },
    remove(id: string) { try { storage.removeItem(key(id)); } catch { /* Best effort. */ } },
  };
}
