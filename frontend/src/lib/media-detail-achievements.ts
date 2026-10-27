import {editorStore,type EditorTitle} from './editor-store';
import {watchAchievementCatalogue,type AchievementCatalogue} from './game-achievement-catalogue';
import {gameProgress} from './game-progress';
import {mountDetailsMotion} from './content-motion';

/** Catalogue evidence is shared; unlocked rows are fetched only for this viewer. */
export function mountDetailAchievements(root:HTMLElement,id:number,signal:AbortSignal){
  const host=root.querySelector<HTMLElement>('[data-detail-achievements]');if(!host)return ()=>{};
  const tab=root.querySelector<HTMLButtonElement>('[data-detail-tab="achievements"]')!;
  const heading=host.querySelector<HTMLElement>('[data-achievement-progress]')!;
  const status=host.querySelector<HTMLElement>('[data-achievement-status]')!;
  const rows=host.querySelector<HTMLElement>('[data-achievement-rows]')!;
  const more=host.querySelector<HTMLButtonElement>('[data-achievement-more]')!;
  let title=editorStore.findSubject({kind:'game',catalogue_id:id});
  let catalogue:AchievementCatalogue|undefined,offset=0,total=0,busy=false,loaded=false;
  let request:AbortController|undefined,generation=0,needsReset=false;
  let definitionError='',sourceStatus:{pending?:boolean;error?:string;state?:string}|undefined;
  const definitions=new Map<number,any>();
  const paintStatus=()=>{
    status.textContent=definitionError||
      (sourceStatus?.error?'Some achievement data is temporarily unavailable.':
      sourceStatus?.pending||busy&&!loaded?'Loading achievements…':
      loaded&&!total?(catalogue?.total===0?'No achievements available.':'Achievement definitions are unavailable.'):
      catalogue?.availability==='partial'?'Achievement data is partial.':
      catalogue&&catalogue.total==null?'Achievement availability is unknown.':'');
    status.hidden=!status.textContent;
  };
  const state=()=>title?.entry?.achievements||title?.game_state?.achievements||{};
  const visible=()=>tab.getAttribute('aria-selected')==='true';
  const paint=()=>{
    const summary={...state(),...(catalogue?.total!=null?{total:catalogue.total}: {})};
    const progress=gameProgress({achievements:summary}).achievement;
    heading.textContent=summary.mode?`Your progress${progress?` · ${progress}`:''}`:'Not tracked';
    for(const card of rows.querySelectorAll<HTMLElement>('[data-achievement-id]')){
      const row=definitions.get(Number(card.dataset.achievementId));
      const earned=card.querySelector<HTMLElement>('[data-achievement-earned]')!;
      earned.hidden=!row.unlocked;earned.textContent=summary.mode==='targeted'?'Unlocked':'Saved selection';
    }
  };
  const append=(row:any)=>{
    definitions.set(row.id,row);
    const card=document.createElement(row.hidden&&!row.unlocked?'details':'article');card.className='lab-season-card game-detail-achievement';card.dataset.achievementId=String(row.id);
    if(card instanceof HTMLDetailsElement){const summary=document.createElement('summary');summary.textContent='Hidden achievement';card.append(summary);mountDetailsMotion(card,signal);}
    const body=document.createElement('div');body.className='game-detail-achievement-body';
    const art=row.unlocked?row.icon_url:row.locked_icon_url||row.icon_url;
    if(art){const image=document.createElement('img');image.alt='';image.loading='lazy';image.width=image.height=48;image.src=art;body.append(image);}
    const copy=document.createElement('div'),name=document.createElement('h3');name.textContent=row.name;copy.append(name);
    if(row.description){const description=document.createElement('p');description.textContent=row.description;copy.append(description);}
    const earned=document.createElement('span');earned.dataset.achievementEarned='';copy.append(earned);body.append(copy);
    if(row.global_percentage!=null){const global=document.createElement('span');global.className='game-achievement-global';global.textContent=`${Number(row.global_percentage.toFixed(1))}%`;global.title='Global unlock percentage';body.append(global);}
    card.append(body);rows.append(card);
  };
  async function load(){
    if(signal.aborted||busy||!visible())return;
    busy=true;more.disabled=true;request=new AbortController();const active=generation,reset=needsReset;paintStatus();
    try{
      const response=await fetch(`/api/proxy/tracking/games/works/${id}/achievements?offset=${reset?0:offset}&limit=50`,{signal:request.signal,cache:'no-store'});
      if(!response.ok)throw new Error('Achievements are temporarily unavailable.');
      const page=await response.json();if(signal.aborted||active!==generation)return;
      if(reset){rows.replaceChildren();definitions.clear();offset=0;needsReset=false;}
      for(const row of page.results)append(row);
      total=page.total;offset+=page.results.length;loaded=true;more.hidden=offset>=total||!page.results.length;
      definitionError='';more.textContent='Show more';paint();paintStatus();
    }catch(error){if(!signal.aborted&&active===generation&&(error as Error).name!=='AbortError'){definitionError='Achievements are temporarily unavailable.';paintStatus();more.hidden=false;more.textContent='Retry';}}
    finally{if(active===generation){busy=false;more.disabled=false;paintStatus();}}
  }
  watchAchievementCatalogue(id,signal,(data,source)=>{
    if(catalogue&&data.definitions_available&&(catalogue.total!==data.total||!catalogue.definitions_available||catalogue.availability!==data.availability)){
      request?.abort();generation++;busy=false;loaded=false;needsReset=true;
    }
    catalogue=data;sourceStatus=source;
    const known=!!state().mode||!!data.total||data.definitions_available&&data.total!==0;
    const empty=data.total===0&&!source?.pending&&!state().mode;
    if(known){tab.hidden=false;tab.disabled=false;}
    if(empty){
      if(visible())root.querySelector<HTMLButtonElement>('[data-detail-tab="overview"]')?.click();
      const focused=document.activeElement===tab||host.contains(document.activeElement);tab.hidden=true;tab.disabled=true;tab.tabIndex=-1;
      if(focused)root.querySelector<HTMLButtonElement>('[data-detail-tab="overview"]')?.focus({preventScroll:true});
    }
    paintStatus();
    paint();if(visible()&&!loaded)void load();
  });
  more.addEventListener('click',()=>void load(),{signal});
  document.addEventListener('anylist:editor-state',event=>{
    const next=(event as CustomEvent<EditorTitle>).detail;if(next.type!=='game'||next.id!==id)return;
    title=next;if(state().mode){tab.hidden=false;tab.disabled=false;}paint();
  },{signal});
  document.addEventListener('anylist:entry-saved',event=>{
    const saved=(event as CustomEvent).detail;if(saved.subject?.kind!=='game'||saved.subject.catalogue_id!==id)return;
    request?.abort();generation++;busy=false;loaded=false;needsReset=true;if(visible())void load();
  },{signal});
  signal.addEventListener('abort',()=>request?.abort(),{once:true});
  paint();return ()=>{if(!loaded)void load();};
}
