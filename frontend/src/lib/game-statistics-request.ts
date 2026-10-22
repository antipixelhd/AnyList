import type {GameStatsResponse,GameStatsSection,GameStatsSort} from './game-statistics-data';

export interface GameStatsSelection {section:GameStatsSection;sort:GameStatsSort;page:number;generation?:number|null}
export function createGameStatsLoader(username:string,callbacks:{render:(data:GameStatsResponse)=>void;error:(message:string,denied:boolean)=>void;busy:(busy:boolean)=>void;changed:()=>void}) {
  let active:AbortController|undefined,stopped=false;
  return {
    async load(selection:GameStatsSelection) {
      if(stopped)return;
      active?.abort();const current=active=new AbortController();callbacks.busy(true);
      const params=new URLSearchParams({section:selection.section,sort:selection.sort,page:String(selection.page)});
      if(selection.generation!=null)params.set('generation',String(selection.generation));
      try {
        const response=await fetch(`/api/proxy/tracking/games/users/${encodeURIComponent(username)}/statistics?${params}`,{signal:current.signal,cache:'no-store'});
        if(stopped||current.signal.aborted)return;
        if(response.status===409){callbacks.changed();return;}
        if(response.redirected||!response.ok){callbacks.error(response.status===403?'Games access or this profile is unavailable.':'Unable to load Games statistics.',response.redirected||[401,403,404].includes(response.status));return;}
        const data:GameStatsResponse=await response.json();
        if(!stopped&&!current.signal.aborted)callbacks.render(data);
      } catch {if(!stopped&&!current.signal.aborted)callbacks.error('Unable to load Games statistics. Try again.',false);}
      finally {if(!stopped&&active===current)callbacks.busy(false);}
    },
    stop(){stopped=true;active?.abort();},
  };
}
