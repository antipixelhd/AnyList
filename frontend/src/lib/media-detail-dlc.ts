import {editorStore,type EditorTitle} from './editor-store';
import {mountDetailsMotion} from './content-motion';
import {responsiveArtwork} from './responsive-artwork';
import {formatDate} from './date-time';
import {gameStatuses} from './game-editor-data';
import {scoreLabel} from './tracker';

export function mountDetailDlc(root:HTMLElement,id:number,signal:AbortSignal){
  const host=root.querySelector<HTMLElement>('[data-game-dlc]');
  if(!host)return ()=>{};
  const rows=host.querySelector<HTMLElement>('[data-game-dlc-rows]')!;
  const status=host.querySelector<HTMLElement>('[data-game-dlc-status]')!;
  const more=host.querySelector<HTMLButtonElement>('[data-game-dlc-more]')!;
  let offset=0,busy=false,started=false,hasMore=true;
  const cards=new Map<number,{title:EditorTitle;button:HTMLButtonElement;score:HTMLElement}>();
  const paint=(title:EditorTitle)=>{
    const card=cards.get(title.id!);if(!card)return;
    card.title=title;
    const state=title.entry||(title.game_class==='expansion'?null:title.game_state);
    const adding=title.game_class==='expansion'&&!state;
    card.button.textContent=adding?'Add to list':state?.status?gameStatuses.find(([value])=>value===state.status)?.[1]||'Edit':'Edit';
    card.button.setAttribute('aria-label',`${adding?'Add to list:':'Edit'} ${title.title}`);
    card.score.hidden=!state;
    card.score.textContent=scoreLabel(state?.score??null);
  };
  const append=(raw:EditorTitle)=>{
    if(cards.has(raw.id!))return;
    const title=editorStore.seed(raw),card=document.createElement('article');card.className='lab-season-card game-dlc-card';
    const details=document.createElement('details'),summary=document.createElement('summary');summary.className='game-dlc-head';
    const art=responsiveArtwork(title.poster,{size:'w185',sizes:'64px',route:'direct'});
    if(art){const image=document.createElement('img');image.alt='';image.loading='lazy';image.src=art.src;if(art.srcset)image.srcset=art.srcset;image.sizes=art.sizes||'64px';summary.append(image);}
    const copy=document.createElement('div');copy.className='game-dlc-copy';
    const heading=document.createElement('h3');heading.textContent=title.title;
    const facts=document.createElement('p');facts.textContent=[title.game_class==='expansion'?'Expansion':'DLC',title.release_date?formatDate(title.release_date):null].filter(Boolean).join(' · ');
    copy.append(heading,facts);summary.append(copy);
    const arrow=host.querySelector<HTMLTemplateElement>('[data-game-dlc-arrow]')!.content.cloneNode(true);
    summary.append(arrow);
    const body=document.createElement('div');body.className='game-dlc-body';
    const synopsis=document.createElement('p');synopsis.textContent=title.overview||'No synopsis available yet.';
    body.append(synopsis);
    if(title.game_class==='expansion'){
      const link=document.createElement('a');link.href=`/games/${title.id}`;link.className='lab-text-button';link.textContent='View expansion';body.append(link);
    }
    const actions=document.createElement('div');actions.className='game-dlc-actions';
    const score=document.createElement('strong');score.className='game-dlc-score';
    score.setAttribute('aria-label','Your rating');
    const button=document.createElement('button');button.type='button';button.className='lab-text-button';
    button.addEventListener('click',()=>{
      const current=cards.get(title.id!)!.title;
      document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject:current.subject,opener:button,title:current}}));
    },{signal});
    actions.append(score,button);body.append(actions);details.append(summary,body);card.append(details);rows.append(card);
    cards.set(title.id!,{title,button,score});paint(title);mountDetailsMotion(details,signal);
  };
  const load=async()=>{
    if(busy||!hasMore||signal.aborted)return;
    started=true;busy=true;more.disabled=true;status.hidden=offset>0;status.textContent='Loading DLC…';
    try{
      const response=await fetch(`/api/proxy/tracking/games/works/${id}/children?offset=${offset}&limit=24`,{signal,cache:'no-store'});
      if(!response.ok)throw new Error('DLC is temporarily unavailable.');
      const data=await response.json();if(signal.aborted)return;
      data.results.forEach(append);offset+=data.results.length;hasMore=data.has_more;
      const empty=data.total===0&&data.coverage==='complete';
      const tab=root.querySelector<HTMLButtonElement>('[data-detail-tab="dlc"]');
      if(tab&&empty){
        const focused=document.activeElement===tab||host.contains(document.activeElement);
        if(tab.getAttribute('aria-selected')==='true')root.querySelector<HTMLButtonElement>('[data-detail-tab="overview"]')?.click();
        tab.hidden=true;tab.disabled=true;tab.tabIndex=-1;
        if(focused)root.querySelector<HTMLButtonElement>('[data-detail-tab="overview"]')?.focus({preventScroll:true});
      }
      status.textContent=data.total?'':empty?'No playable DLC is available.':'DLC metadata has not been verified yet.';status.hidden=data.total>0;
      more.textContent='Show more';more.hidden=!hasMore;
    }catch(cause){if(!signal.aborted){status.hidden=false;status.textContent=(cause as Error).message;more.hidden=false;more.textContent='Retry';}}
    finally{busy=false;more.disabled=false;}
  };
  more.addEventListener('click',()=>void load(),{signal});
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<EditorTitle>).detail;if(title.type==='game')paint(title);
  },{signal});
  // Optional child metadata never holds up primary title/artwork/tracking.
  const timer=setTimeout(()=>{if(!started&&!signal.aborted)void load();},1500);
  signal.addEventListener('abort',()=>clearTimeout(timer),{once:true});
  return ()=>{if(!started)void load();};
}
