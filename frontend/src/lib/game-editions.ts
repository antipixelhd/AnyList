import {detailBackground} from './detail-background';
import {reveal} from './ui-motion';

/** Optional edition evidence starts after primary content and never owns tracking state. */
export function mountGameEditions(root:HTMLElement,id:number,signal:AbortSignal,primaryPending=false){
  const card=root.querySelector<HTMLElement>('[data-game-editions]');if(!card)return;
  const links=card.querySelector<HTMLElement>('[data-game-edition-links]')!;
  const template=card.querySelector<HTMLTemplateElement>('[data-game-edition-template]')!;
  const resource=detailBackground<any>(`/api/proxy/tracking/games/works/${id}/editions`,signal,data=>{
    const entering=card.hidden;
    const rows=(data.editions||[]).filter((row:any)=>Number.isSafeInteger(row.id)&&row.id>0&&row.id!==id&&typeof row.title==='string'&&Number.isInteger(row.year));
    links.replaceChildren(...rows.map((row:any)=>{
      const link=template.content.firstElementChild!.cloneNode(true) as HTMLAnchorElement;
      link.href=`/games/${row.id}`;
      link.querySelector('[data-game-edition-title]')!.textContent=row.title;
      link.querySelector('[data-game-edition-year]')!.textContent=String(row.year);
      return link;
    }));
    card.hidden=!rows.length;if(entering&&rows.length)reveal(card);
    return data.status;
  },()=>{});
  let idle:number|undefined,timer:ReturnType<typeof setTimeout>|undefined;
  const start=()=>{
    if(signal.aborted)return;
    if('requestIdleCallback' in window)idle=window.requestIdleCallback(()=>resource.start(),{timeout:2000});
    else timer=setTimeout(()=>resource.start(),250);
  };
  if(primaryPending)root.addEventListener('anylist:game-primary',start,{once:true,signal});else start();
  signal.addEventListener('abort',()=>{if(idle!==undefined)window.cancelIdleCallback(idle);clearTimeout(timer);},{once:true});
}
