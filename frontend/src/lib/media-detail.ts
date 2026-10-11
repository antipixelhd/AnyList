import { editorStore, seedEditorPage, type EditorTitle } from './editor-store';
import { createContentTransition } from './content-motion';
import { mountContributorBiography } from './contributor-biography';
import { showDialog, closeDialog, reveal } from './ui-motion';
import { initializeScrollMotion } from './scroll-motion';
import { setupScoreSteppers } from './scoreStepper';
import { showToast } from './toasts';
import {kindLabel,relationLabel} from './media-detail-data';

let pageListeners: AbortController | undefined;
export function mountMediaDetail() {
  pageListeners?.abort();
  const root = document.querySelector<HTMLElement>('[data-media-detail]');
  if (!root) return;
  pageListeners = new AbortController();
  const {signal} = pageListeners;
  document.addEventListener('astro:before-swap', () => pageListeners?.abort(), {signal,once:true});
  seedEditorPage();
  const info = JSON.parse(root.querySelector('#media-detail-data')!.textContent!);
  if(info.relatedPending){
    const icons=root.querySelector<HTMLTemplateElement>('[data-detail-work-icons]')!.content;
    void fetch(`/api/proxy/tracking/title/${info.id}/related`,{signal}).then(async response=>{
      if(!response.ok)return;
      const data=await response.json();
      if(signal.aborted)return;
      for(const field of ['relations','similar'] as const){
        const section=root.querySelector<HTMLElement>(`[data-detail-${field==='relations'?'related':'similar'}]`)!;
        const grid=section.querySelector<HTMLElement>('[class$="-grid"]')!;
        const rows=(Array.isArray(data[field])?data[field]:[]).filter((row:any)=>field!=='similar'||row.type===info.type);
        const cards=rows.map((work:any)=>{
          const card=document.createElement('a');
          const url=new URL(work.url,location.origin);
          if(url.origin!==location.origin&&url.protocol!=='https:')return null;
          card.href=url.href;
          if(field==='relations')card.className='lab-related-card';
          if(work.poster){const image=document.createElement('img');image.src=work.poster;image.alt='';image.loading='lazy';card.append(image);}
          else card.append(icons.firstElementChild!.cloneNode(true));
          const copy=document.createElement('div');
          if(field==='relations'){const label=document.createElement('span');label.className='lab-relation-type';label.textContent=work.label||relationLabel(work.relation,work.reverse);copy.append(label);}
          const title=document.createElement('h3');title.textContent=work.title;
          const detail=document.createElement('p');detail.textContent=[kindLabel[work.type as keyof typeof kindLabel],work.year].filter(Boolean).join(' · ');
          copy.append(title,detail);card.append(copy);
          if(field==='similar')card.append(icons.lastElementChild!.cloneNode(true));
          return card;
        }).filter((card:HTMLAnchorElement|null):card is HTMLAnchorElement=>!!card);
        grid.replaceChildren(...cards);section.hidden=!cards.length;
        if(cards.length&&section.getBoundingClientRect().top<innerHeight&&section.getBoundingClientRect().bottom>0)reveal(section);
      }
    }).catch(()=>{});
  }
  let title = editorStore.find(info.id);
  const synopsis = root.querySelector<HTMLElement>('[data-detail-synopsis]');
  if (synopsis) mountContributorBiography(synopsis,signal);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!signal.aborted) initializeScrollMotion(root,signal,'.lab-module','media-scroll-reveal', {
      animateInitial:false,downwardOnly:true,once:true,
    });
  }));

  const tabs = [...root.querySelectorAll<HTMLButtonElement>('[data-detail-tab]')];
  const panels = [...root.querySelectorAll<HTMLElement>('[data-detail-panel]')];
  const switchPanel = createContentTransition(root.querySelector<HTMLElement>('[data-detail-panels]')!,signal);
  const selectTab = (id: string, animate = true) => {
    if (!tabs.some(tab => tab.dataset.detailTab === id)) return;
    const change = () => {
      tabs.forEach(tab => { const active = tab.dataset.detailTab === id; tab.setAttribute('aria-selected',String(active)); tab.tabIndex = active ? 0 : -1; });
      panels.forEach(panel => panel.hidden = panel.dataset.detailPanel !== id);
    };
    if (animate) void switchPanel(change); else change();
    const url = new URL(location.href);
    if (id === 'overview') url.searchParams.delete('tab'); else url.searchParams.set('tab',id);
    history.replaceState(history.state,'',url);
  };
  selectTab(new URLSearchParams(location.search).get('tab') || 'overview',false);
  tabs.forEach((tab,index) => {
    tab.addEventListener('click', () => selectTab(tab.dataset.detailTab!), {signal});
    tab.addEventListener('keydown', event => {
      const next = event.key === 'ArrowRight' ? (index+1)%tabs.length : event.key === 'ArrowLeft' ? (index-1+tabs.length)%tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : null;
      if (next == null) return;
      event.preventDefault(); tabs[next].focus(); selectTab(tabs[next].dataset.detailTab!);
    }, {signal});
  });
  root.querySelectorAll<HTMLButtonElement>('[data-detail-open-tab]').forEach(button => button.addEventListener('click', () => {
    selectTab(button.dataset.detailOpenTab!);
    tabs.find(tab=>tab.dataset.detailTab===button.dataset.detailOpenTab)?.focus({preventScroll:true});
  }, {signal}));

  const favorite = root.querySelector<HTMLButtonElement>('[data-favorite-toggle]');
  const library = root.querySelector<HTMLButtonElement>('[data-library-toggle]');
  const note = root.querySelector<HTMLTextAreaElement>('#detail-note');
  const seasonInputs = [...root.querySelectorAll<HTMLInputElement>('[data-season]')];
  const dirtySeasons = new Set<string>();
  seasonInputs.forEach(input=>input.addEventListener('input',()=>dirtySeasons.add(input.dataset.season!),{signal}));
  let noteDirty = false;
  note?.addEventListener('input',()=>{noteDirty=true;},{signal});
  const paint = (state: EditorTitle) => {
    title = state;
    if (favorite) {
      const active = !!state.entry?.favorite;
      favorite.dataset.favorite=String(active); favorite.setAttribute('aria-pressed',String(active));
      favorite.title=active?'Remove from favorites':'Add to favorites'; favorite.setAttribute('aria-label',favorite.title);
    }
    if (library) {
      const active = !!state.editor_library?.desired;
      library.dataset.desired=String(active); library.setAttribute('aria-pressed',String(active));
      library.title=active?'Remove from library':'Add to library'; library.setAttribute('aria-label',library.title);
    }
    if (note && !noteDirty) note.value=state.entry?.notes || '';
    const hasSeasons=Object.values(state.entry?.season_scores || {}).some(Boolean);
    seasonInputs.forEach(input=>{
      if(!dirtySeasons.has(input.dataset.season!))input.value=String(state.entry?.season_scores?.[input.dataset.season!] ?? '');
    });
    root.querySelectorAll<HTMLElement>('[data-save-season]').forEach(button=>button.dataset.hasSeasons=hasSeasons?'yes':'no');
    const rating = root.querySelector<HTMLElement>('[data-quick-rate-id]');
    if (rating) {
      const score = state.entry?.score || null;
      rating.dataset.quickRateScore=score==null?'':String(score);
      rating.dataset.quickRateMode=state.entry?.rating_mode || 'manual';
      rating.querySelector('[data-score-label]')!.textContent=score==null?'—':Number(Number(score).toFixed(1)).toString();
      rating.setAttribute('aria-label',`Rate ${state.title}. Current score: ${score ?? 'Unrated'}`);
    }
  };
  document.addEventListener('anylist:editor-state', event => {
    const state = (event as CustomEvent<EditorTitle>).detail;
    if (state.id===info.id) paint(state);
  }, {signal});
  if (title) paint(title);
  favorite?.addEventListener('click',()=>{
    if (!title) return;
    const desired=!title.entry?.favorite;
    void editorStore.write(title,{favorite:desired}).then(saved=>{
      if (title?.entry?.favorite===saved.favorite) showToast({message:desired?'Added to favorites.':'Removed from favorites.',kind:'success',key:'detail-favorite'});
    }).catch(error=>showToast(error.message,'error'));
  },{signal});
  library?.addEventListener('click',()=>{
    if (!title || library.disabled) return;
    const desired=!title.editor_library?.desired;
    void editorStore.writeLibrary(title,desired).then(saved=>{
      if (title?.editor_library?.desired===saved.desired) showToast({message:desired?'Added to library.':'Removed from library.',kind:'success',key:'detail-library'});
    }).catch(error=>showToast(error.message,'error'));
  },{signal});
  root.querySelector<HTMLButtonElement>('[data-detail-save-note]')?.addEventListener('click',async event=>{
    if (!title || !note) return;
    const button=event.currentTarget as HTMLButtonElement;
    const notes=note.value; button.disabled=true; button.setAttribute('aria-busy','true');
    try { await editorStore.write(title,{notes}); if (note.value===notes) noteDirty=false; showToast('Notes saved.','success'); }
    catch (error) { showToast(error instanceof Error?error.message:'Unable to save notes.','error'); }
    finally { button.disabled=false; button.removeAttribute('aria-busy'); }
  },{signal});

  const lightbox=root.querySelector<HTMLDialogElement>('#detail-lightbox')!;
  const image=lightbox.querySelector<HTMLImageElement>('[data-detail-lightbox-image]')!;
  const caption=lightbox.querySelector<HTMLElement>('p')!;
  const images=[...root.querySelectorAll<HTMLButtonElement>('[data-detail-image]')];
  const unique=[...new Map(images.map(button=>[button.dataset.detailImage!,button])).values()];
  let currentImage=0, imageRevision=0;
  const showImage=(index:number)=>{
    currentImage=(index+unique.length)%unique.length;
    const button=unique[currentImage]; const revision=++imageRevision;
    image.src=button.dataset.detailImage!; image.alt=button.dataset.detailCaption || '';
    caption.textContent=image.alt;
    lightbox.querySelectorAll<HTMLButtonElement>('[data-detail-image-step]').forEach(arrow=>arrow.hidden=unique.length<2);
    void image.decode().catch(()=>{}).then(()=>{if(revision===imageRevision&&!signal.aborted)reveal(image);});
  };
  images.forEach(button=>button.addEventListener('click',()=>{showImage(unique.findIndex(item=>item.dataset.detailImage===button.dataset.detailImage));showDialog(lightbox);},{signal}));
  lightbox.querySelector('[data-detail-close-lightbox]')!.addEventListener('click',()=>{void closeDialog(lightbox);},{signal});
  lightbox.addEventListener('click',event=>{if(event.target===lightbox)void closeDialog(lightbox);},{signal});
  lightbox.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();showImage(currentImage+(event.key==='ArrowLeft'?-1:1));}},{signal});
  lightbox.querySelectorAll<HTMLButtonElement>('[data-detail-image-step]').forEach(button=>button.addEventListener('click',()=>showImage(currentImage+Number(button.dataset.detailImageStep)),{signal}));

  const seasons=root.querySelector<HTMLElement>('.seasons-section');
  if (seasons) setupScoreSteppers(seasons);
  const request=async(path:string,body?:unknown)=>{
    const response=await fetch(`/api/proxy/tracking/${path}`,{method:body?'PATCH':'POST',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Unable to save this change.');
    return data;
  };
  root.querySelector<HTMLButtonElement>('#refresh-episodes')?.addEventListener('click',async event=>{
    const button=event.currentTarget as HTMLButtonElement;button.disabled=true;button.setAttribute('aria-busy','true');
    try{await request(`title/${info.id}/refresh-episodes`);location.reload();}
    catch(error){showToast((error as Error).message,'error');button.disabled=false;button.removeAttribute('aria-busy');}
  },{signal});
  root.querySelectorAll<HTMLButtonElement>('[data-season-progress]').forEach(button=>button.addEventListener('click',async()=>{
    button.disabled=true;
    const body={watched:button.dataset.watched==='true',confirm_rollback:false};
    try {
      try{await request(`entry/${info.id}/season/${button.dataset.seasonProgress}`,body);}
      catch(error){if(!(error as Error).message.includes('Confirm to continue'))throw error;if(!confirm((error as Error).message))return;body.confirm_rollback=true;await request(`entry/${info.id}/season/${button.dataset.seasonProgress}`,body);}
      location.reload();
    }catch(error){showToast((error as Error).message,'error');}
    finally{button.disabled=false;}
  },{signal}));
  const mode=root.querySelector<HTMLDialogElement>('#season-mode');
  mode?.addEventListener('click',event=>{if(event.target===mode)void closeDialog(mode,'cancel');},{signal});
  root.querySelectorAll<HTMLButtonElement>('[data-save-season]').forEach(button=>button.addEventListener('click',async()=>{
    const input=root.querySelector<HTMLInputElement>(`[data-season="${button.dataset.saveSeason}"]`)!;
    if(!input.reportValidity()||!title)return;
    const score=Number(input.value||0);
    const patch:Record<string,unknown>={season_scores:{[button.dataset.saveSeason!]:score}};
    if(button.dataset.hasSeasons==='no'&&Number(input.value)>0&&mode){
      mode.returnValue='cancel';showDialog(mode);
      const result=await new Promise<string>(resolve=>{
        const done=()=>{mode.removeEventListener('close',done);signal.removeEventListener('abort',done);resolve(signal.aborted?'cancel':mode.returnValue);};
        mode.addEventListener('close',done,{once:true});signal.addEventListener('abort',done,{once:true});
      });
      if(!['manual','average'].includes(result)||signal.aborted)return;patch.rating_mode=result;
    }
    button.disabled=true;
    try{await editorStore.write(title,patch);if(Number(input.value||0)===score)dirtySeasons.delete(input.dataset.season!);showToast('Season score saved.','success');}
    catch(error){showToast((error as Error).message,'error');}
    finally{button.disabled=false;}
  },{signal}));
  if(new URLSearchParams(location.search).get('rate')==='1')requestAnimationFrame(()=>{if(!signal.aborted)root.querySelector<HTMLButtonElement>('[data-quick-rate-id]')?.click();});
}
