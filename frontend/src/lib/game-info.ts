import {detailBackground,type DetailLoadState} from './detail-background';
import {gameAgeRating,requirementRows,type GameInfo} from './game-info-data';
import {deviceStoreCountry} from './store-country';
import {reveal} from './ui-motion';

type InfoResponse={info:GameInfo|null;status:DetailLoadState;country:string;country_source:string};
export function mountGameInfo(root:HTMLElement,id:number,signal:AbortSignal,initial?:{info:GameInfo|null;country:string;country_source:string}) {
  const section=root.querySelector<HTMLElement>('[data-game-requirements]');if(!section)return;
  const ages=[...root.querySelectorAll<HTMLElement>('[data-game-age-rating]')];
  const age=(info:GameInfo|null,country:string)=>{
    const value=gameAgeRating(info,country);
    for(const element of ages){element.hidden=!value;if(value){element.querySelector('dd')!.textContent=value.label;element.title=value.detail;}}
  };
  if(initial)age(initial.info,initial.country_source==='account'?initial.country:deviceStoreCountry());
  const resource=detailBackground<InfoResponse>(`/api/proxy/tracking/games/works/${id}/info?country=${deviceStoreCountry()}`,signal,data=>{
    age(data.info,data.country);const entering=section.hidden;
    for(const part of ['minimum','recommended'] as const){
      const container=section.querySelector<HTMLElement>(`[data-requirements-part="${part}"]`)!,rows=requirementRows(data.info,part);
      container.hidden=!rows.length;const list=container.querySelector('dl')!;
      const content=document.createDocumentFragment();
      for(const row of rows){const group=document.createElement('div');if(row.label){const term=document.createElement('dt');term.textContent=row.label;group.append(term);}const value=document.createElement('dd');value.textContent=row.value;group.append(value);content.append(group);}
      list.replaceChildren(content);
    }
    section.hidden=!requirementRows(data.info,'minimum').length&&!requirementRows(data.info,'recommended').length;
    const link=section.querySelector<HTMLAnchorElement>('[data-game-requirements-source]');
    if(link){link.hidden=!data.info?.steam_app_id;if(data.info?.steam_app_id)link.href=`https://store.steampowered.com/app/${data.info.steam_app_id}/#app_system_requirements`;}
    if(entering&&!section.hidden)reveal(section);
    return data.status;
  },()=>{});
  resource.start();
}
