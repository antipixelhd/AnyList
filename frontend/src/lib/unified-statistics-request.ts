import type {MediaScope} from './stats-overview-data';
import type {GameStatsResponse} from './game-statistics-data.ts';
import {gameStatisticsView,type StatisticsDomain,type UnifiedStatistics} from './unified-statistics-data.ts';
export interface StatisticsSelection {domain:StatisticsDomain;media:MediaScope;section:string;sort:string;page:number;generation?:number|null}
export function statisticsRequestKey(value:StatisticsSelection){return value.domain==='screen'?`screen:${value.media}`:`game:${value.section}:${value.sort}:${value.page}`;}
export function createStatisticsReader(username:string,signal:AbortSignal,selection:()=>StatisticsSelection,callbacks:{render:(data:UnifiedStatistics)=>void;onError:(message:string,denied:boolean)=>void;onBusy:(busy:boolean,quiet?:boolean)=>void;changed:()=>void},request:typeof fetch=fetch){
  const cache=new Map<string,UnifiedStatistics>(),flights=new Map<string,Promise<UnifiedStatistics>>();let revision=0,selected=0;
  const save=(key:string,data:UnifiedStatistics)=>{
    if(data.status!=='ready'||data.refreshing)return;
    const domain=key.split(':')[0];
    if(data.generation!=null&&[...cache].some(([old,value])=>old.startsWith(domain+':')&&value.generation!=null&&value.generation>data.generation!))return;
    for(const [old,value] of cache)if(old.startsWith(domain+':')&&value.generation!==data.generation)cache.delete(old);
    cache.delete(key);cache.set(key,data);while(cache.size>12)cache.delete(cache.keys().next().value!);
  };
  const clear=()=>{revision++;cache.clear();flights.clear();};
  const read=(value:StatisticsSelection)=>{
    const key=statisticsRequestKey(value),stored=cache.get(key);if(stored)return Promise.resolve(stored);
    const existing=flights.get(key);if(existing)return existing;
    const epoch=revision;
    const query=new URLSearchParams(value.domain==='game'?{section:value.section,sort:value.sort,page:String(value.page)}:{media_type:value.media});
    if(value.domain==='game'&&value.page>1&&value.generation!=null)query.set('generation',String(value.generation));
    const path=value.domain==='game'?`games/users/${encodeURIComponent(username)}/statistics`:`profile/${encodeURIComponent(username)}/stats/overview`;
    const pending=(async()=>{
      const response=await request(`/api/proxy/tracking/${path}?${query}`,{signal,cache:'no-store'});
      if(response.redirected||!response.ok)throw Object.assign(new Error(response.status===403?'This profile or media domain is unavailable.':'Unable to load statistics.'),{status:response.redirected?401:response.status});
      const payload=await response.json();const data=value.domain==='game'?gameStatisticsView(payload as GameStatsResponse,value.sort):payload as UnifiedStatistics;
      if(signal.aborted||epoch!==revision)throw new DOMException('Discarded statistics','AbortError');save(key,data);return data;
    })().finally(()=>{if(flights.get(key)===pending)flights.delete(key);});
    flights.set(key,pending);return pending;
  };
  return {
    seed(value:StatisticsSelection,data:UnifiedStatistics){save(statisticsRequestKey(value),data);},
    clear,
    async prefetch(value:StatisticsSelection){try{await read(value);}catch{/* Speculation never replaces usable data. */}},
    async load(_media?:MediaScope,quiet=false){
      const version=++selected,value=selection();
      quiet=quiet||cache.has(statisticsRequestKey(value));callbacks.onBusy(true,quiet);
      try{const data=await read(value);if(!signal.aborted&&version===selected)callbacks.render(data);}
      catch(error){if(signal.aborted||version!==selected||(error as Error).name==='AbortError')return;const status=(error as {status?:number}).status;if(status===409){cache.clear();callbacks.changed();}else {const denied=[401,403,404].includes(status||0);if(denied)clear();callbacks.onError((error as Error).message,denied);}}
      finally{if(!signal.aborted&&version===selected)callbacks.onBusy(false,quiet);}
    },
    stop(){selected++;},
  };
}
