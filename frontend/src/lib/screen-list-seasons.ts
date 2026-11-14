import type {EditorTitle} from './editor-store';
import {panelActive} from './profile-panel';
import {applyResponsiveArtwork} from './responsive-artwork';
import {scoreLabel} from './tracker';

type Season = {number:number; name:string|null; poster:string|null; year:string|null;
  total:number|null; watched:number; score:number|null; completed:boolean; upcoming:boolean};
type Summary = {order:string; seasons:Season[]; pending:boolean; retry_after:number|null; unavailable:boolean};
type Expansion = {entry:HTMLElement; toggle:HTMLButtonElement; panel:HTMLElement; title:string;
  request?:AbortController; retry?:ReturnType<typeof setTimeout>; motion?:Animation; summary?:Summary};

function paintScore(row:HTMLElement,score:number|null) {
  row.querySelector('[data-score-label]')!.textContent=scoreLabel(score);
  const control=row.querySelector<HTMLButtonElement>('[data-quick-rate-season]');
  if(control)control.dataset.quickRateScore=String(score??'');
}

export function mountScreenListSeasons(root:HTMLElement,profilePanel:HTMLElement,template:HTMLTemplateElement,signal:AbortSignal) {
  const owner=root.dataset.owner==='true';
  const profile=root.dataset.listEndpoint!.replace(/\/[^/]+$/,'');
  const expanded=new Map<number,Expansion>();

  function animate(item:Expansion,from:number,closing=false) {
    const {panel}=item;
    item.motion?.cancel();
    const to=closing?0:panel.scrollHeight;
    const finish=()=>{
      if(closing)panel.remove();
      delete panel.dataset.animating;
    };
    if(!panel.isConnected||Math.abs(to-from)<1||matchMedia('(prefers-reduced-motion: reduce)').matches){finish();return;}
    panel.dataset.animating='';
    const motion=panel.animate([{height:`${from}px`},{height:`${to}px`}],{duration:200,easing:'cubic-bezier(.2,.7,.2,1)'});
    item.motion=motion;
    void motion.finished.then(()=>{if(item.motion===motion){item.motion=undefined;finish();}},()=>{});
  }

  function close(item:Expansion,withMotion:boolean) {
    expanded.delete(Number(item.entry.dataset.listEntry));
    item.request?.abort();clearTimeout(item.retry);
    item.toggle.setAttribute('aria-expanded','false');
    item.toggle.removeAttribute('aria-controls');
    item.panel.inert=true;
    if(withMotion)animate(item,item.panel.getBoundingClientRect().height,true);
    else{item.motion?.cancel();item.panel.remove();}
  }

  function message(item:Expansion,text:string,retry=false) {
    const message=document.createElement('p');
    message.className='list-season-message';message.setAttribute('role','status');message.textContent=text;
    if(retry){
      const button=document.createElement('button');button.type='button';button.className='lab-text-button';button.textContent='Retry';
      button.addEventListener('click',()=>{message.textContent='Loading seasons…';void load(item);});message.append(button);
    }
    item.panel.replaceChildren(message);
  }

  function paint(item:Expansion,summary:Summary) {
    const height=item.panel.getBoundingClientRect().height;
    item.motion?.cancel();
    item.panel.replaceChildren(...summary.seasons.map(season=>{
      const row=template.content.firstElementChild!.cloneNode(true) as HTMLElement;
      const label=season.name||`Season ${season.number}`;
      row.dataset.listSeason=String(season.number);
      row.querySelector('[data-season-name]')!.textContent=label;
      const year=row.querySelector<HTMLElement>('[data-season-year]')!;
      year.textContent=season.year;year.hidden=!season.year;
      const thumbnail=row.querySelector<HTMLElement>('.entry-thumbnail')!;
      const image=thumbnail.querySelector<HTMLImageElement>('img')!;
      if(season.poster){
        thumbnail.dataset.previewSrc=season.poster;
        applyResponsiveArtwork(image,season.poster,{size:'w185',sizes:'44px',route:'direct'});
      }else image.replaceWith(Object.assign(document.createElement('span'),{className:image.className}));
      row.querySelector('[data-season-progress]')!.textContent=season.total==null?String(season.watched):`${season.watched}/${season.total}`;
      const status=row.querySelector('[data-season-status]');
      if(status)status.textContent=season.completed?'Completed':season.watched?'Watching':season.upcoming?'Upcoming':'Not started';
      const rate=row.querySelector<HTMLButtonElement>('[data-quick-rate-season]');
      if(rate){
        Object.assign(rate.dataset,{quickRateId:item.entry.dataset.listEntry,quickRateSeason:String(season.number),
          quickRateOrder:summary.order,quickRateTitle:item.title,quickRatePoster:season.poster||''});
        rate.setAttribute('aria-label',`Rate ${item.title}, ${label}`);
      }
      paintScore(row,season.score);
      return row;
    }));
    if(!summary.seasons.length){
      if(summary.unavailable)message(item,'Unable to load seasons.',true);
      else message(item,summary.pending?'Loading seasons…':'No seasons in progress yet.');
    }
    animate(item,height);
  }

  async function load(item:Expansion) {
    clearTimeout(item.retry);item.request?.abort();
    const request=new AbortController();item.request=request;
    const id=item.entry.dataset.listEntry;
    try{
      const response=await fetch(`/api/proxy/tracking/${profile}/series/${id}/seasons`,{
        signal:AbortSignal.any([signal,request.signal,AbortSignal.timeout(15_000)]),cache:'no-store',
      });
      if(!response.ok||response.redirected)throw new Error('Season list read failed');
      const summary:Summary=await response.json();
      if(signal.aborted||request.signal.aborted||!panelActive(profilePanel)||!item.entry.isConnected)return;
      // A pending refresh can return the same usable summary several times.
      if(!item.summary||item.summary.unavailable!==summary.unavailable||!summary.seasons.length&&item.summary.pending!==summary.pending||
          item.summary.order!==summary.order||JSON.stringify(item.summary.seasons)!==JSON.stringify(summary.seasons))paint(item,summary);
      item.summary=summary;
      if(summary.pending)item.retry=setTimeout(()=>void load(item),Math.max(1,summary.retry_after||2)*1000);
    }catch{
      if(!signal.aborted&&!request.signal.aborted&&!item.summary?.seasons.length)message(item,'Unable to load seasons.',true);
    }finally{
      if(item.request===request)item.request=undefined;
    }
  }

  root.addEventListener('click',event=>{
    const toggle=(event.target as Element).closest<HTMLButtonElement>('[data-season-toggle]');
    const entry=toggle?.closest<HTMLElement>('[data-list-entry]');
    if(!toggle||!entry)return;
    const id=Number(entry.dataset.listEntry),existing=expanded.get(id);
    if(existing){close(existing,true);return;}
    entry.querySelector('.list-season-panel')?.remove();
    const panel=document.createElement('div');panel.className='list-season-panel';
    panel.id=`list-seasons-${root.dataset.screenScope}-${id}`;
    toggle.setAttribute('aria-controls',panel.id);toggle.setAttribute('aria-expanded','true');
    const item={entry,toggle,panel,title:toggle.querySelector('[data-list-title-label]')?.textContent||''};
    expanded.set(id,item);
    message(item,'Loading seasons…');entry.append(panel);animate(item,0);void load(item);
  },{signal});
  root.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    const row=(event.target as Element).closest<HTMLElement>('[data-list-entry]');
    const item=expanded.get(Number(row?.dataset.listEntry));
    if(item){item.toggle.focus({preventScroll:true});close(item,true);}
  },{signal});

  if(owner){
    document.addEventListener('anylist:editor-committed',event=>{
      const {title,patch}=(event as CustomEvent<{title:EditorTitle;patch:Record<string,unknown>|null}>).detail;
      if(title.type!=='series'||!title.id)return;
      const item=expanded.get(title.id);
      if(!item)return;
      if(!title.entry)close(item,false);
      else if(patch&&['progress','status','series_editor','finish_date'].some(key=>key in patch))void load(item);
    },{signal});
    document.addEventListener('media-tracker:rating-changed',event=>{
      const {id,season_number,episode_order,score}=(event as CustomEvent).detail;
      const item=expanded.get(id);
      if(!item?.summary||item.summary.order!==episode_order)return;
      const season=item.summary.seasons.find(season=>season.number===season_number);
      const row=item.panel.querySelector<HTMLElement>(`[data-list-season="${Number(season_number)}"]`);
      if(!season||!row)return;
      season.score=score;paintScore(row,score);
      if(item.summary.pending||item.request)void load(item);
    },{signal});
  }
  const clear=()=>{for(const item of expanded.values())close(item,false);};
  profilePanel.addEventListener('anylist:panel-inactive',clear,{signal});
  signal.addEventListener('abort',clear,{once:true});

  return {
    remove(entry:HTMLElement){
      const id=Number(entry.dataset.listEntry),item=expanded.get(id);
      // A progress write can move the same node to another status group.
      if(item)queueMicrotask(()=>{if(!entry.isConnected&&expanded.get(id)===item)close(item,false);});
    },
  };
}
