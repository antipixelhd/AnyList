import type { BrowseItem } from './browse-card-actions';

export interface ContributorWork extends BrowseItem {
  entry: Record<string, any> | null;
  key: string; href: string | null; release_date: string | null; roles: string[]; characters: string[];
}
export interface ContributorPage {
  kind: 'actor' | 'staff' | 'studio'; key: string; name: string; image: string | null; description: string | null;
  banner?: string | null;
  birthday: string | null; deathday: string | null; place_of_birth: string | null; department: string | null;
  aliases: string[]; countries: string[]; headquarters: string | null; links: {label:string;href:string}[];
  roles: string[]; works: ContributorWork[]; known_works: number; list_count: number | null;
  total: number | null; page: number; has_more: boolean; next_cursor: string | null; source: 'local' | 'provider'; notice: string | null;
}
export function contributorParams(search: URLSearchParams, signedIn: boolean) {
  const params = new URLSearchParams();
  const type = search.get('media_type');
  if (type === 'movie' || type === 'series') params.set('media_type', type);
  const list = search.get('list_scope');
  if (signedIn && (list === 'in' || list === 'out')) params.set('list_scope', list);
  const status = search.get('status');
  if (signedIn && ['watching','completed','planning','paused','dropped'].includes(status || '')) params.set('status', status!);
  const page = Number(search.get('page'));
  if (Number.isInteger(page) && page > 1 && page <= 500) params.set('page', String(page));
  if (search.get('cursor')) params.set('cursor', search.get('cursor')!.slice(0,400));
  if (search.get('source') === 'local' || search.get('source') === 'provider') params.set('source', search.get('source')!);
  return params;
}
export function contributorYear(work: Pick<ContributorWork, 'release_date' | 'year'>) {
  const year = (work.release_date || work.year || '').slice(0,4);
  return /^[0-9]{4}$/.test(year) ? year : 'Undated';
}
export function workHref(work: BrowseItem & {href?:string|null}) {
  return work.id ? `/title/${work.id}` : work.tmdb_id ? `/discover-title?type=${work.type}&id=${work.tmdb_id}` : work.href || null;
}

export function biographyPreview(value: string | null | undefined, limit = 60) {
  const full = (value || '').trim();
  const first = full.split(/[\r\n]+/)[0] || '';
  const words = first.split(/\s+/).filter(Boolean);
  const shortened = words.length > limit;
  const preview = words.slice(0, limit).join(' ') + (shortened ? '…' : '');
  return {preview, full, expandable:shortened || full.replace(/\s+/g, ' ') !== preview};
}
