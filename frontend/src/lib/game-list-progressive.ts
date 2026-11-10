import {gameStatuses} from './game-editor-data.ts';
import {compareGameListEntries} from './game-list-data.ts';

export const gameListRenderBatch=30;
export const gameListRenderAhead=600;
export const gameListViewCookie='game-list-view';
export type GameListState=Record<string,any>;

/** Complete membership stays available to filters independently of mounted rows. */
export function gameListCollection(entries:GameListState[],query:URLSearchParams){
  const byId=new Map(entries.map(entry=>[entry.subject.catalogue_id,entry]));
  const eligible=entries.filter(entry=>entry.listed&&(!entry.base_work_id||byId.get(entry.base_work_id)?.listed));
  const counts:Record<string,number>={};
  for(const entry of eligible)counts[entry.status]=(counts[entry.status]||0)+1;
  const search=(query.get('q')||'').toLowerCase();
  const matches=eligible.filter(entry=>(!query.get('status')||entry.status===query.get('status'))&&
    entry.title.toLowerCase().includes(search)&&(!query.get('genre')||entry.genres?.includes(query.get('genre')))&&
    (!query.get('year')||entry.year===query.get('year'))&&(!query.has('favorites')||entry.favorite));
  const groups=new Map(gameStatuses.map(([status])=>[status,matches.filter(entry=>entry.status===status)
    .sort((a,b)=>compareGameListEntries(a,b,query.get('sort')||'title'))]));
  return {counts,groups,total:matches.length,eligible};
}

export function nextGameListLimit(current:number,total:number,bottom:number,viewport:number){
  return current<total&&bottom<=viewport+gameListRenderAhead?Math.min(total,current+gameListRenderBatch):current;
}

/** Only render counts are persisted, scoped to this viewer, profile and query. */
export function gameListSavedLimits(raw:string|undefined,key:string):Record<string,number>{
  if(!raw)return {};
  try{
    let value;
    try{value=JSON.parse(raw);}catch{value=JSON.parse(decodeURIComponent(raw));}
    if(value?.key!==key||!value.limits||typeof value.limits!=='object')return {};
    return Object.fromEntries(gameStatuses.map(([status])=>[status,
      Number.isSafeInteger(value.limits[status])&&value.limits[status]>=gameListRenderBatch?value.limits[status]:gameListRenderBatch]));
  }catch{return {};}
}
