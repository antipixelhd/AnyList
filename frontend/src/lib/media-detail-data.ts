import type { EditorTitle } from './editor-store';
import {countryLabel} from './stats-overview-data.ts';
import {formatDate,localToday} from './date-time.ts';

export type MediaKind = 'movie' | 'series' | 'game' | 'book';
export type DetailPerson = {id: number; kind: 'actor' | 'staff' | 'studio' | 'character'; card_key?:string; performances?:{id:number;name:string;image?:string|null}[]; name: string; image?: string | null; character_image?: string | null; character_images?: string[]; url: string | null; roles: string[]; role_codes?: string[]; position?: number | null; credit_order?:number; episode_count?:number; producer_episode_count?:number; episode_total?:number; characters: string[]};
export function detailCreditEpisodes(person:DetailPerson) {
  const count=person.producer_episode_count,total=person.episode_total;
  if(person.kind!=='staff'||!person.roles.some(role=>/^(executive )?producer$/i.test(role))||
      person.roles.some(role=>/^(creator|showrunner|head writer)$/i.test(role))||
      typeof count!=='number'||!Number.isInteger(count)||count<1||
      (typeof total==='number'&&Number.isInteger(total)&&count>=total))return '';
  return `${count} ${count===1?'episode':'episodes'}`;
}
export function detailCastPresentation(person: DetailPerson, mode: 'actors' | 'characters') {
  const character = person.characters.filter(Boolean).join(' / ');
  return {primary: mode === 'characters' && character ? character : person.name,
    secondary: mode === 'characters' ? character ? person.name : '' : character || person.roles.join(', '),
    images: [...new Set((mode === 'characters' ? [person.character_image, ...(person.character_images || []), person.image] : [person.image]).filter((url):url is string=>!!url))]};
}
export type DetailMedia = EditorTitle & {
  id: number; type: MediaKind; overview?: string | null; year?: string; release_date?: string | null;
  original_title?: string; tagline?: string; release_status?: string; original_language?: string;
  runtime?: number; episode_runtime?: number; season_count?: number; episode_count?: number;
  budget?: number | null; revenue?: number | null;
  genres: string[]; seasons: any[]; friends: any[]; friends_average?: number | null;
  people: DetailPerson[]; organizations: DetailPerson[]; posters: string[]; artwork: string[]; relations: any[]; similar: any[]; related_pending?: boolean;
  character_status?: {state:string;pending?:boolean;error?:string|null;retry_after?:number};
  game_characters?:import('./game-cast-data').GameCharacter[];
  game_character_status?:{complete?:boolean;source?:string};
  game_credit_status?: {state:string;pending?:boolean;error?:string|null;retry_after?:number;complete?:boolean;coverage?:string};
  completion_estimates?:import('./game-time-to-beat').CompletionEstimates|null;
  game_offer_context?:Omit<import('./game-offer-data').GameOffers,'offers'>;
  game_ratings?:import('./game-ratings-data').GameRatings|null;
  game_metadata_status?:{primary_ready:boolean;state:string;pending?:boolean;error?:string|null;retry_after?:number};
  game_info?:import('./game-info-data').GameInfo|null;
  image_status?: {state:string;error?:string|null;retry_after?:number};
  background_status?: {state:string;error?:string|null;retry_after?:number};
  backdrop_fallback?:string;
  backdrop_fallbacks?:string[];
  preview_status?:{state:string;retry_after?:number};
  artwork_preview?:{image?:string|null;thumbnail?:string|null};
  artwork_thumbnails?:Record<string,string>;
  scores_pending?: boolean;
  sources: {name: string; url: string}[]; editions: any[]; releases: any[]; prices: any[];
  platforms?:string[];
  countries: string[]; pages?: number; creators?: {name: string}[]; networks?: {name: string}[];
  tmdb_score?: number | null; imdb_score?: number | null; rt_critic_score?: number | null; rt_audience_score?: number | null;
  imdb_id?: string; tvdb_id?: number; tracking_available?: boolean;
  base_game?:{id:number;title:string;image_url?:string|null}|null;
};
export const kindLabel = {movie:'Movie', series:'Series', game:'Game', book:'Book'};
// Browse's canonical movie/series genre IDs; provider-specific names stay local.
const browseGenres: Record<string, number> = {
  Action:28, Adventure:12, Animation:16, Comedy:35, Crime:80, Documentary:99,
  Drama:18, Family:10751, Fantasy:14, History:36, Horror:27, Music:10402,
  Mystery:9648, Romance:10749, 'Science Fiction':878, 'TV Movie':10770,
  Thriller:53, War:10752, Western:37, 'Action & Adventure':10759, Kids:10762,
  News:10763, Reality:10764, 'Sci-Fi & Fantasy':10765, Soap:10766, Talk:10767,
  'War & Politics':10768,
};
const seriesGenreIds = new Set([16,35,80,99,18,10751,9648,37,10759,10762,10763,10764,10765,10766,10767,10768]);
const sharedGenreIds = new Set([16,35,80,99,18,10751,9648,37]);
export function detailBrowseHref(type: MediaKind, options: {year?: string; genre?: string} = {}) {
  if (type !== 'movie' && type !== 'series') return null;
  const params = new URLSearchParams({type});
  if (options.year && /^\d{4}$/.test(options.year)) {
    params.set('start', `${options.year}-01-01`);
    params.set('end', `${options.year}-12-31`);
    params.set('view', 'timeline');
  }
  if (options.genre) {
    const id=browseGenres[options.genre];
    const canonical=id && (type==='series' ? seriesGenreIds.has(id) : !seriesGenreIds.has(id)||sharedGenreIds.has(id));
    params.set('genres', canonical ? String(id) : `name:${options.genre}`);
  }
  return `/browse?${params}`;
}
export function detailCreators(people: DetailPerson[]) {
  return people.filter(person => person.kind !== 'actor' && person.roles.some(role => /^(director|supervising director|creator|showrunner|head writer|author|developer)$/i.test(role.trim()))).slice(0,2);
}
export function detailWorkCaption(work: {type?: MediaKind; genres?: string[]; year?: string}, similar = false) {
  return [similar ? work.genres?.find(genre => typeof genre === 'string' && genre.trim())?.trim() : work.type ? kindLabel[work.type] : null, work.year].filter(Boolean).join(' · ');
}
const nameKey=(name:string)=>name.trim().toLocaleLowerCase().replace(/\s+/g,' ');
const roleText=(person:DetailPerson)=>[...person.roles,...(person.role_codes||[])].join(' ');
export function detailCredits(media:DetailMedia) {
  const position=(person:DetailPerson)=>person.credit_order??person.position??Number.MAX_SAFE_INTEGER;
  const screen=media.type==='movie'||media.type==='series';
  const cast=media.people.filter(person=>person.kind==='actor').sort((a,b)=>position(a)-position(b)).slice(0,screen?(media.type==='movie'?16:24):undefined);
  const priority=(person:DetailPerson)=>{
    const roles=media.type==='game'?[...person.roles,...(person.role_codes||[])]:person.roles.length?person.roles:person.role_codes||[];
    const ranks=[/^(director|supervising director|creator|showrunner|head writer)$/i,/^(writer|screenplay|screenwriter|teleplay|story|author|novel|book|play|original story|characters|characters created by)$/i,/^(cinematographer|cinematography|director of photography|composer|original music composer|editor|film editor|production design|production designer|costume design|costume designer|casting|casting director|visual effects supervisor|animation director)$/i,/^(producer|developer)$/i,...(media.type==='series'?[/^executive producer$/i]:[])];
    const rank=ranks.findIndex(pattern=>roles.some(role=>pattern.test(role.trim())));
    return rank<0?99:rank;
  };
  const crew=media.people.filter(person=>person.kind!=='actor').sort((a,b)=>priority(a)-priority(b)||position(a)-position(b));
  const companies=new Map<string,DetailPerson>();
  for(const person of media.organizations.filter(person=>/producer|production|studio|developer|publisher/i.test(roleText(person)))){
    const key=String(person.id),previous=companies.get(key);
    companies.set(key,previous?{...previous,url:previous.url||person.url,image:previous.image||person.image,
      roles:[...new Set([...previous.roles,...person.roles])],role_codes:[...new Set([...(previous.role_codes||[]),...(person.role_codes||[])])]}:person);
  }
  const studios=[...companies.values()];
  const principal=screen?crew.filter(person=>priority(person)<99).slice(0,16):crew;
  return {cast,principal,supporting:[],studios,
    preview:cast.length?cast.slice(0,8):principal.slice(0,8)};
}
export function detailOrganizationFacts(media:DetailMedia) {
  const groups:{label:string;rows:{name:string;url?:string|null}[]}[]=[];
  const seen=new Set<string>();
  const add=(label:string,rows:{name:string;url?:string|null}[])=>{
    const unique=rows.filter(row=>{const key=nameKey(row.name);if(!key||seen.has(key))return false;seen.add(key);return true;});
    if(unique.length)groups.push({label,rows:unique});
  };
  add('Created by',media.creators||[]);
  // The title's primary TMDB networks take precedence over alternate regional
  // broadcasters/distributors from catalogue enrichment (e.g. YouTube rentals).
  const networks=media.organizations.filter(person=>/broadcaster|network/i.test(roleText(person)));
  add('Network',media.networks?.length?media.networks.map(network=>({name:network.name,url:networks.find(row=>nameKey(row.name)===nameKey(network.name))?.url})):networks);
  const production=detailCredits(media).studios;
  if(media.type==='book')add('Publisher',production);
  else if(media.type==='game'){
    add('Developer',production.filter(person=>/developer/i.test(roleText(person))));
    add('Publisher',production.filter(person=>/publisher/i.test(roleText(person))));
  }else add('Studios',production);
  return groups;
}
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
  return formatDate(value?.slice(0, 10));
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
export function detailFacts(media: DetailMedia, today = localToday()) {
  const facts: {label: string; value: string}[] = [];
  const add = (label: string, value: unknown) => { if (value != null && value !== '' && value !== 0) facts.push({label,value:String(value)}); };
  const release = (media.release_date || media.year || '').slice(0,10);
  const future = /^\d{4}(?:-\d{2}){0,2}$/.test(release) && release > today.slice(0,release.length);
  add(media.type === 'book' ? 'First published' : future ? 'Releases' : 'Released', detailDate(media.release_date) || media.year);
  add('Status',media.release_status);
  if (media.type === 'movie') add('Runtime',durationLabel(media.runtime));
  if (media.type === 'series') {
    add('Episode runtime',durationLabel(media.episode_runtime)); add('Seasons',media.season_count); add('Episodes',media.episode_count);
  }
  add('Language',media.original_language?.toUpperCase());
  add('Country',media.countries.map(countryLabel).join(', '));
  if (media.type === 'movie') {
    const dollars = new Intl.NumberFormat('en', {style:'currency',currency:'USD',maximumFractionDigits:0});
    for (const [label, value] of [['Budget', media.budget], ['Box office', media.revenue]] as const) {
      if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) add(label, dollars.format(value));
    }
  }
  if (media.type === 'book') add('Pages',media.pages);
  if (media.type === 'game') add('Platforms',[...new Set([...(media.platforms||[]),...media.releases.map(release => release.platform)].filter(Boolean))].join(', '));
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
