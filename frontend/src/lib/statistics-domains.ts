import {profilePanelKey} from './profile-panel.ts';

const sections={stats:['overview','genres','actors','studios','staff'],'stats/games':['overview','genres','developers','publishers','actors','staff']};
/** Preserve only a section offered by the destination; its own filters stay local. */
export function statisticsDomainUrl(current:URL,target:URL){
  const from=profilePanelKey(current),to=profilePanelKey(target),url=new URL(target);
  if(!from||!to||from.username!==to.username||!(from.key in sections)||!(to.key in sections)||from.key===to.key)return url;
  const selected=current.searchParams.get('section')||'overview';
  const section=sections[to.key as keyof typeof sections].includes(selected)?selected:'overview';
  if(section!==(url.searchParams.get('section')||'overview'))url.searchParams.delete('page');
  url.searchParams.set('section',section);return url;
}

/** Small, viewer-local section projections; never cache pending or failed generations. */
export function createStatisticsSections<T extends {status:string;generation:number|null}>(limit=12){
  const values=new Map<string,T>();let generation:number|null|undefined;
  return {
    get(key:string){return values.get(key);},
    set(key:string,value:T){
      if(value.status!=='ready')return;
      if(generation!==value.generation){values.clear();generation=value.generation;}
      values.delete(key);values.set(key,value);
      while(values.size>limit)values.delete(values.keys().next().value!);
    },
    clear(){values.clear();generation=undefined;},
  };
}
