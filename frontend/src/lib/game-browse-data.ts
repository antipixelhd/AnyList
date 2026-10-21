import {formatDateTime} from './date-time.ts';
export const gameBrowseCategories=[['trending','Trending'],['most_played','Most Played'],['sellers','Top sellers'],['newest','Newest'],['popular','All-time popular'],['score','Highest rated']] as const;
const filters=['q','genres','platforms','start','end','min_metacritic'];
export function gameBrowseValues(input:URLSearchParams){
  const values=new URLSearchParams();
  for(const key of filters){const value=(input.get(key)||'').trim();if(value&&value!=='0')values.set(key,value);}
  const sort=input.get('sort')||input.get('category')||'all';
  if(gameBrowseCategories.some(([key])=>key===sort))values.set('sort',sort);
  if(input.get('view')==='timeline'){values.set('sort','newest');values.set('view','timeline');}
  if(values.get('sort')&&values.get('sort')!=='trending'||values.get('view')==='timeline')values.delete('min_metacritic');
  return values;
}
export function gameBrowseIsCategories(values:URLSearchParams){
  return !values.has('sort')&&!values.has('view')&&!gameBrowseHasFilters(values);
}
export const gameBrowseHasFilters=(values:URLSearchParams)=>filters.some(key=>!!values.get(key)&&values.get(key)!=='0');
export function gameBrowseRequest(values:URLSearchParams,limit=24,cursor?:string|null,today=new Date().toISOString().slice(0,10)){
  const params=new URLSearchParams(values);params.delete('view');params.delete('sort');
  if(values.get('view')==='timeline'){
    params.set('timeline','true');
    if(!params.has('end')&&!(params.get('start')!>today))params.set('end',today);
  }
  params.set('category',values.get('sort')||'trending');params.set('limit',String(limit));
  if(cursor)params.set('cursor',cursor);
  return `games/browse?${params}`;
}
export function gameBrowseHref(values:URLSearchParams,changes:Record<string,string|null>={}){
  const result=new URLSearchParams(values);for(const [key,value] of Object.entries(changes)){if(value)result.set(key,value);else result.delete(key);}
  const next=gameBrowseValues(result);return `/browse/games${next.size?'?'+next:''}`;
}
export type GamePlatformGroup={id:string;name:string;devices:{id:string;name:string}[]};
export function gamePlatformSelection(values:string[],groups:GamePlatformGroup[]){
  return groups.flatMap(group=>{const devices=group.devices.filter(device=>values.includes(device.id));return devices.length?devices:values.includes(group.id)?[{id:group.id,name:group.name}]:[];});
}
export function gameSourceContext(page:{source?:string|null;source_at?:number|null;fetched_at?:string|null;partial?:boolean;stale?:boolean;coverage_limited?:boolean}){
  return [page.source,page.source_at?formatDateTime(page.source_at*1000,'datetime'):page.fetched_at?formatDateTime(page.fetched_at,'datetime'):null,page.stale?'Cached ranking':null,page.partial?'Partial source coverage':null,page.coverage_limited?'Source scan limit reached':null].filter(Boolean).join(' · ');
}
