import {gameCategoryName} from './game-category-store.ts';
export type BrowseIdentity={id?:number|null;tmdb_id?:number|null;igdb_id?:number|null;type:string;href?:string;release_event?:string|null;game_class?:string;base_listed?:boolean|null};
export function browseCardKey(item:BrowseIdentity){
  return item.release_event||`${item.type}:${item.type==='game'?item.igdb_id?`igdb:${item.igdb_id}`:`catalogue:${item.id}`:item.tmdb_id||item.id}`;
}
export function browseCardHref(item:BrowseIdentity){
  if(item.type==='game')return item.id?`/games/${item.id}`:item.igdb_id?`/discover-game?id=${item.igdb_id}`:item.href;
  return item.id?`/title/${item.id}`:item.tmdb_id?`/discover-title?type=${item.type}&id=${item.tmdb_id}`:item.href;
}
export function browseActionLabel(action:string,game=false){
  return action==='edit'?'Open list editor for':action==='plan'?game?'Plan to play':'Plan to watch':action==='watch'?game?`Set to ${gameCategoryName('playing')}`:'Set to Watching':'Rate and complete';
}
export const browseStatusLabel=(status:string,game=false)=>game?(status?gameCategoryName(status):''):({watching:'Watching',playing:'Playing',completed:'Completed',planning:game?'Planned':'Planning',paused:'Paused',dropped:'Dropped'} as Record<string,string>)[status]||'';
export const expansionCard=(item:BrowseIdentity)=>item.type==='game'&&item.game_class==='expansion';
export const browseCardCanEdit=(item:BrowseIdentity)=>!expansionCard(item)||item.base_listed!==false;
export const browseCardQuickActions=(item:BrowseIdentity)=>!expansionCard(item)||item.base_listed===true;
