import type { EditorTitle } from './editor-store';
import {countryLabel} from './stats-overview-data';

export type MediaKind = 'movie' | 'series' | 'game' | 'book';
export type DetailPerson = {id: number; kind: 'actor' | 'staff' | 'studio'; name: string; image?: string | null; url: string | null; roles: string[]; characters: string[]};
export type DetailMedia = EditorTitle & {
  id: number; type: MediaKind; overview?: string | null; year?: string; release_date?: string | null;
  original_title?: string; tagline?: string; release_status?: string; original_language?: string;
  runtime?: number; episode_runtime?: number; season_count?: number; episode_count?: number;
  genres: string[]; seasons: any[]; friends: any[]; friends_average?: number | null;
  people: DetailPerson[]; organizations: DetailPerson[]; posters: string[]; artwork: string[]; relations: any[]; similar: any[]; related_pending?: boolean;
  sources: {name: string; url: string}[]; editions: any[]; releases: any[]; prices: any[];
  countries: string[]; pages?: number; creators?: {name: string}[]; networks?: {name: string}[];
  tmdb_score?: number | null; imdb_score?: number | null; rt_critic_score?: number | null; rt_audience_score?: number | null;
  imdb_id?: string; tvdb_id?: number; tracking_available?: boolean;
};
export const kindLabel = {movie:'Movie', series:'Series', game:'Game', book:'Book'};
export function localMediaId(value: string | undefined) {
  return /^[1-9][0-9]{0,9}$/.test(value || '') && Number(value) <= 2147483647 ? Number(value) : null;
}
export function prepareDetail(raw: Record<string, any>): DetailMedia {
  const detail: Record<string, any> = {...raw, entry:raw.entry || null};
  for (const field of ['genres','seasons','friends','people','organizations','posters','artwork','relations','similar','sources','editions','releases','prices','countries']) {
    detail[field] = Array.isArray(raw[field]) ? raw[field] : [];
  }
  detail.similar=detail.similar.filter((work:any)=>work.type===raw.type);
  return detail as DetailMedia;
}
export function durationLabel(minutes?: number | null) {
  if (!minutes || minutes < 0) return '';
  const hours = Math.floor(minutes / 60), rest = Math.round(minutes % 60);
  return hours ? `${hours}h${rest ? ` ${rest}m` : ''}` : `${rest}m`;
}
export function detailDate(value?: string | null) {
  if (!value) return '';
  if (/^\d{4}$/.test(value)) return value;
  const date = new Date(value.slice(0, 10));
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en', {dateStyle:'medium',timeZone:'UTC'}).format(date);
}
export function detailSynopsis(value?: string | null) {
  const full=(value||'').trim();
  const words=[...full.matchAll(/\S+/g)];
  const shortened=words.length>120;
  const end=shortened?words[119].index!+words[119][0].length:full.length;
  const lead=full.slice(0,end),rest=full.slice(end);
  return {lead,rest,shortened,expandable:shortened};
}
export function progressTotal(media: DetailMedia) {
  if (media.type === 'movie') return 1;
  const seasons = media.seasons.filter(season => season.season_number > 0);
  return seasons.some(season => season.released_count != null)
    ? seasons.reduce((sum, season) => sum + Number(season.released_count || 0), 0) : media.episode_count || null;
}
export function detailFacts(media: DetailMedia) {
  const facts: {label: string; value: string}[] = [];
  const add = (label: string, value: unknown) => { if (value != null && value !== '' && value !== 0) facts.push({label,value:String(value)}); };
  add(media.type === 'book' ? 'First published' : 'Released', detailDate(media.release_date) || media.year);
  add('Release status',media.release_status);
  if (media.type === 'movie') add('Runtime',durationLabel(media.runtime));
  if (media.type === 'series') {
    add('Episode runtime',durationLabel(media.episode_runtime)); add('Seasons',media.season_count); add('Episodes',media.episode_count);
  }
  add('Language',media.original_language?.toUpperCase());
  add('Country',media.countries.map(countryLabel).join(', '));
  if (media.type === 'book') add('Pages',media.pages);
  if (media.type === 'game') add('Platforms',[...new Set(media.releases.map(release => release.platform).filter(Boolean))].join(', '));
  return facts;
}
export function relationLabel(relation: string, reverse = false) {
  const labels: Record<string, [string,string]> = {dlc:['DLC for','DLC'],expansion:['Expansion of','Expansion'],
    standalone_expansion:['Expansion of','Standalone expansion'],remake:['Remake of','Remake'],remaster:['Remaster of','Remaster'],
    port:['Port of','Port'],version:['Version of','Version'],series_member:['Series','Series'],recommendation:['Related','Related'],
    adaptation:['Adaptation of','Adaptation'],based_on:['Source','Adaptation'],prequel:['Prequel to','Sequel'],sequel:['Sequel to','Prequel'],spin_off:['Spin-off of','Spin-off'],shared_universe:['Shared universe','Shared universe']};
  return labels[relation]?.[reverse ? 1 : 0] || 'Related';
}
export function moneyLabel(value: {currency?: string; amount?: number; amountInt?: number} | null | undefined) {
  if (!value?.currency || !/^[A-Z]{3}$/.test(value.currency)) return null;
  try {
    const format = new Intl.NumberFormat('en', {style:'currency',currency:value.currency});
    const digits = format.resolvedOptions().maximumFractionDigits ?? 2;
    const amount = typeof value.amount === 'number' ? value.amount : typeof value.amountInt === 'number' ? value.amountInt / 10 ** digits : NaN;
    return Number.isFinite(amount) && amount >= 0 ? format.format(amount) : null;
  } catch { return null; }
}
