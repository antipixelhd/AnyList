import {detailBackground,type DetailLoadState} from './detail-background';
import {gameAgeRating,hasSteamRequirements,requirementRows,type GameInfo} from './game-info-data';
import {gameMobilePlatforms,prepareDetail} from './media-detail-data';
import {deviceStoreCountry} from './store-country';
import {bindGameSection} from './game-section-loading';
import {publishGameRelease,type GameRelease} from './game-primary';

type InfoResponse={info:GameInfo|null;status:DetailLoadState;country:string;country_source:string;release?:GameRelease|null};
export function mountGameInfo(root:HTMLElement,id:number,signal:AbortSignal,initial:{platforms:string[];info:GameInfo|null;country:string;country_source:string;status?:DetailLoadState},setRequirementsAvailable:(available:boolean)=>void) {
  const section=root.querySelector<HTMLElement>('[data-game-requirements]');if(!section)return;
  const ages=[...root.querySelectorAll<HTMLElement>('[data-game-age-rating]')];
  const age=(info:GameInfo|null,country:string)=>{
    const value=gameAgeRating(info,country);
    for(const element of ages){element.hidden=!value;if(value){element.querySelector('dd')!.textContent=value.label;element.title=value.detail;}}
  };
  let info=initial.info,platforms=initial.platforms;
  age(info,initial.country_source==='account'?initial.country:deviceStoreCountry());
  const publishRequirements=()=>{
    const available=hasSteamRequirements(info,platforms);
    if(!available)setRequirementsAvailable(false);
    for(const part of ['minimum','recommended'] as const){
      const container=section.querySelector<HTMLElement>(`[data-requirements-part="${part}"]`)!,rows=available?requirementRows(info,part):[];
      container.hidden=!rows.length;const list=container.querySelector('dl')!;
      const content=document.createDocumentFragment();
      for(const row of rows){const group=document.createElement('div');if(row.label){const term=document.createElement('dt');term.textContent=row.label;group.append(term);}const value=document.createElement('dd');value.textContent=row.value;group.append(value);content.append(group);}
      list.replaceChildren(content);
    }
    const link=section.querySelector<HTMLAnchorElement>('[data-game-requirements-source]')!;
    link.hidden=!available;
    if(available)link.href=`https://store.steampowered.com/app/${info!.steam_app_id}/#app_system_requirements`;
    else link.removeAttribute('href');
    if(available)setRequirementsAvailable(true);
  };
  root.addEventListener('anylist:game-primary',event=>{
    const data=(event as CustomEvent).detail;
    if(data.id!==id)return;
    platforms=gameMobilePlatforms(prepareDetail(data));
    publishRequirements();
  },{signal});
  const resource=detailBackground<InfoResponse>(`/api/proxy/tracking/games/works/${id}/info?country=${deviceStoreCountry()}`,signal,data=>{
    publishGameRelease(root,data.release,signal);
    age(data.info,data.country);
    info=data.info;
    publishRequirements();
    return data.status;
  },()=>{});
  bindGameSection(root,section,signal,resource,initial.status);
}
