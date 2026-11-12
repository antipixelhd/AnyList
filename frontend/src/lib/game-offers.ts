import {detailBackground} from './detail-background';
import {selectedGameOffer,offerLink,offerRegionMatches,type GameOffers} from './game-offer-data';
import {moneyLabel} from './media-detail-data';
import {setLocalDateTime} from './date-time-dom';
import {deviceStoreCountry,rememberDeviceCountry} from './store-country';
import {startVisibleRefresh} from './visible-refresh';
import {reveal} from './ui-motion';
import {paintGameSection,gameSectionPending,gameSectionFailure} from './game-section-loading';

export function mountGameOffers(root:HTMLElement,id:number,signal:AbortSignal,initial?:GameOffers) {
  const section=root.querySelector<HTMLElement>('[data-game-offer]');if(!section)return;
  const node=<T extends HTMLElement=HTMLElement>(selector:string)=>section.querySelector<T>(selector)!;
  let lifetime:AbortController|undefined,resource:ReturnType<typeof detailBackground<GameOffers>>|undefined,hint='';
  function start() {
    const detected=deviceStoreCountry();rememberDeviceCountry(undefined,detected);
    if(resource&&hint===detected)return resource;
    lifetime?.abort();lifetime=new AbortController();hint=detected;
    const matches=offerRegionMatches(section!.dataset.gameOfferSource,section!.dataset.gameOfferRegion,detected);
    if(!matches){
      paintGameSection(section!,false,{pending:true});
      node('[data-game-offer-low]').hidden=true;node('[data-game-offer-link]').hidden=true;
    }
    resource=detailBackground<GameOffers>(`/api/proxy/tracking/games/works/${id}/offers?country=${detected}`,lifetime.signal,data=>{
      const price=selectedGameOffer(data);section!.dataset.gameOfferSource=data.country_source;section!.dataset.gameOfferRegion=data.country;
      const entering=section!.hidden;paintGameSection(section!,!!price,data.status,root.dataset.gamePrimaryPending==='true');
      if(!price){node('[data-game-offer-low]').hidden=true;node('[data-game-offer-link]').hidden=true;}
      if(price){
        node('[data-game-offer-country]').textContent=price.country;node('[data-game-offer-price]').textContent=moneyLabel(price.current);
        setLocalDateTime(node('.game-offer-updated'),price.fetched_at,'timestamp-date');
        const low=moneyLabel(price.historical_low);node('[data-game-offer-low]').hidden=!low;node('[data-game-offer-low-price]').textContent=low;
        node('[data-game-offer-low-date]').hidden=!price.historical_low_at;setLocalDateTime(node('[data-game-offer-low-date] time'),price.historical_low_at);
        const url=offerLink(price.url),link=node<HTMLAnchorElement>('[data-game-offer-link]');link.hidden=!url;if(url)link.href=url;else link.removeAttribute('href');
        if(entering)reveal(section!);
      }
      return data.status;
    },(state,error)=>gameSectionFailure(section!,state,error));
    if(!matches||gameSectionPending(initial?.status,root.dataset.gamePrimaryPending==='true')||initial?.status.error)resource.start();
    return resource;
  }
  start();
  const visibility=startVisibleRefresh({intervalMs:15*60_000,wakeIntervalMs:15*60_000,refresh:async()=>{await start()?.retry();}});
  window.addEventListener('languagechange',()=>{start();},{signal});
  const needsPrimary=gameSectionPending(initial?.status,root.dataset.gamePrimaryPending==='true')||!initial?.steam_app_id;
  root.addEventListener('anylist:game-primary',()=>{if(needsPrimary)void start()?.retry(true);},{signal});
  root.addEventListener('anylist:game-primary-unavailable',()=>void start()?.retry(true),{signal});
  section.querySelector('[data-game-section-retry]')?.addEventListener('click',()=>void start()?.retry(),{signal});
  signal.addEventListener('abort',()=>{lifetime?.abort();visibility.stop();},{once:true});
}
