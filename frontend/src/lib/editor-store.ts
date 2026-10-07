import { responsiveArtwork } from './responsive-artwork';

export type EditorTitle = {
  id: number | null; tmdb_id?: number | null; type: string; title: string;
  poster?: string | null; backdrop?: string | null;
  entry: Record<string, any> | null;
  editor_library?: { available: boolean; desired: boolean };
};
type RecordState = {
  title: EditorTitle; committed: EditorTitle['entry'];
  pending: { patch: Record<string, any> }[]; tail: Promise<unknown>;
  importing?: Promise<number>; stale?: boolean;
  posterSrc?: string; backdropSrc?: string; backdropReady?: boolean;
  libraryCommitted?: EditorTitle['editor_library']; libraryPending: {desired: boolean}[];
};
type Transport = (path: string, options?: RequestInit) => Promise<any>;

/** One viewer-scoped source for editor reads and ordered optimistic writes. */
export class EditorStore {
  private records = new Map<string, RecordState>();
  private viewer = '';
  private reads = new Map<number, Promise<EditorTitle>>();
  private request: Transport;
  private changed: (title: EditorTitle) => void;
  constructor(request: Transport, changed: (title: EditorTitle) => void = () => {}) {
    this.request = request;
    this.changed = changed;
  }
  scope(viewer: string) {
    if (this.viewer === viewer) return;
    this.viewer = viewer;
    this.records.clear();
    this.reads.clear();
  }
  private key(title: Pick<EditorTitle, 'id' | 'type' | 'tmdb_id'>) {
    return title.tmdb_id ? `${title.type}:${title.tmdb_id}` : `id:${title.id}`;
  }
  private record(title: EditorTitle) {
    return this.records.get(this.key(title)) || [...this.records.values()].find(r => title.id && r.title.id === title.id);
  }
  seed(title: EditorTitle) {
    const existing = this.record(title);
    if (existing) {
      if (!existing.pending.length && !existing.libraryPending.length && !existing.importing) {
        Object.assign(existing.title, title);
        existing.committed = title.entry;
        existing.stale = false;
        existing.libraryCommitted = title.editor_library;
      }
      return existing.title;
    }
    const record: RecordState = {title: {...title}, committed: title.entry, pending: [], tail: Promise.resolve(),
      libraryCommitted:title.editor_library, libraryPending:[]};
    this.records.set(this.key(title), record);
    return record.title;
  }
  get(title: EditorTitle) { return this.record(title)?.title || this.seed(title); }
  find(id: number) { return [...this.records.values()].find(r => r.title.id === id && !r.stale)?.title; }
  async load(id: number) {
    const cached = [...this.records.values()].find(r => r.title.id === id);
    if (cached && !cached.stale) return cached.title;
    const active = this.reads.get(id);
    if (active) return active;
    const viewer = this.viewer;
    const pending = this.request(`editor/${id}`).then(title => {
      if (viewer !== this.viewer) throw new Error('Your account changed. Please try again.');
      return this.seed(title);
    });
    this.reads.set(id, pending);
    try { return await pending; } finally { if (this.reads.get(id) === pending) this.reads.delete(id); }
  }
  invalidate(id: number) {
    const record = [...this.records.values()].find(r => r.title.id === id);
    if (record) record.stale = true;
  }
  acceptSaved(saved: Record<string, any>) {
    const record = [...this.records.values()].find(r => r.title.id === saved.id);
    if (!record || record.pending.length) return;
    this.seed({...record.title, entry:saved.status ? saved : null});
    this.changed(record.title);
  }
  async resolveId(title: EditorTitle): Promise<number> {
    const record = this.record(title)!;
    if (record.title.id) return record.title.id;
    if (!record.importing) {
      record.importing = this.request(`catalog/${title.type}/${title.tmdb_id}`, {method:'POST'})
        .then(result => { record.title.id = result.id; if ([...this.records.values()].includes(record)) this.changed(record.title); return result.id; })
        .finally(() => { record.importing = undefined; });
    }
    return record.importing;
  }
  private paint(record: RecordState) {
    let entry = record.committed;
    for (const operation of record.pending) {
      const previous = entry?.status;
      entry = {...(entry || {status:'planning', progress:0, rating_mode:'manual', manual_score:null,
        season_scores:{}, favorite:false, rewatch_count:0, notes:'', start_date:null, finish_date:null}), ...operation.patch};
      if ('manual_score' in operation.patch) entry.score = operation.patch.manual_score || null;
      const today = new Date().toISOString().slice(0, 10);
      if (entry.status !== previous && entry.status === 'watching' && !entry.start_date && !('start_date' in operation.patch)) entry.start_date = today;
      if (entry.status !== previous && entry.status === 'completed' && !entry.finish_date && !('finish_date' in operation.patch)) entry.finish_date = today;
    }
    record.title.entry = entry;
    if ([...this.records.values()].includes(record)) this.changed(record.title);
  }
  write(title: EditorTitle, patch: Record<string, any>) {
    const viewer = this.viewer;
    const record = this.record(title)!;
    const operation = {patch: {...patch}};
    record.pending.push(operation);
    this.paint(record);
    const task = record.tail.catch(() => {}).then(async () => {
      try {
        if (viewer !== this.viewer) throw new Error('Your account changed. Please try again.');
        const id = await this.resolveId(record.title);
        if (viewer !== this.viewer) throw new Error('Your account changed. Please try again.');
        const saved = await this.request(`entry/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch)});
        record.committed = saved;
        record.stale = false;
        return saved;
      } finally {
        record.pending.splice(record.pending.indexOf(operation), 1);
        this.paint(record);
      }
    });
    record.tail = task;
    return task;
  }
  writeLibrary(title: EditorTitle, desired: boolean) {
    const record = this.record(title)!;
    const viewer = this.viewer;
    const operation = {desired};
    const paint = () => {
      record.title.editor_library = {...(record.libraryCommitted || {available:true, desired:false}),
        desired:record.libraryPending.at(-1)?.desired ?? record.libraryCommitted?.desired ?? false};
      if ([...this.records.values()].includes(record)) this.changed(record.title);
    };
    record.libraryPending.push(operation);
    paint();
    const task = record.tail.catch(() => {}).then(async () => {
      try {
        if (viewer !== this.viewer) throw new Error('Your account changed. Please try again.');
        const id = await this.resolveId(record.title);
        if (viewer !== this.viewer) throw new Error('Your account changed. Please try again.');
        const state = await this.request(`library/${id}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({in_library:desired})});
        record.libraryCommitted = {available:true, desired:state.desired===true};
        return state;
      } finally {
        record.libraryPending.splice(record.libraryPending.indexOf(operation), 1);
        paint();
      }
    });
    record.tail = task;
    return task;
  }
  warmArtwork(title: EditorTitle, posterSrc?: string) {
    const record = this.record(title)!;
    if (posterSrc) record.posterSrc = posterSrc;
    if (!title.backdrop || record.backdropSrc || typeof Image === 'undefined') return;
    record.backdropSrc = responsiveArtwork(title.backdrop, {size:'w1280', route:'direct'})?.src;
    if (!record.backdropSrc) return;
    const image = new Image();
    image.src = record.backdropSrc;
    void image.decode().then(() => { record.backdropReady = true; }).catch(() => {
      record.backdropSrc = undefined;
    });
  }
  artwork(title: EditorTitle, posterSrc?: string) {
    const record = this.record(title)!;
    const poster = posterSrc || record.posterSrc || responsiveArtwork(title.poster, {size:'w342', route:'direct'})?.src;
    // Choose once before opening. Never swap artwork under an active editor.
    return {poster, backdrop: record.backdropReady ? record.backdropSrc : poster};
  }
}

export const editorStore = new EditorStore(async (path, options) => {
  const response = await fetch(`/api/proxy/tracking/${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to save this entry. Try again.');
  return data;
}, title => {
  document.dispatchEvent(new CustomEvent('anylist:editor-state', {detail:title}));
});

export function seedEditorPage() {
  editorStore.scope(document.body.dataset.cacheUser || 'anonymous');
  const list = JSON.parse(document.querySelector('#list-editor-entries')?.textContent || '[]');
  list.forEach((entry: any) => editorStore.seed({...entry, entry:entry.entry ?? entry}));
  const title = JSON.parse(document.querySelector('#title-editor-data')?.textContent || 'null');
  if (title) editorStore.seed(title);
  const browse = JSON.parse(document.querySelector('#browse-data')?.textContent || '{}');
  [...(browse.payload?.results || []), ...(browse.sections || []).flatMap((section: any) => section.results)]
    .forEach((item: any) => { if ('entry' in item) editorStore.seed(item); });
}
