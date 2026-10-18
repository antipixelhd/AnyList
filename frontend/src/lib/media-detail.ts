import { editorStore, seedEditorPage, type EditorTitle } from './editor-store';
import { createContentTransition, mountDetailsMotion } from './content-motion';
import { mountContributorBiography } from './contributor-biography';
import { showDialog, closeDialog, reveal } from './ui-motion';
import { mountDetailImages } from './media-detail-images';
import { mountDetailCast } from './media-detail-cast';
import {detailBackground,type DetailLoadState} from './detail-background';
import { initializeScrollMotion } from './scroll-motion';
import { showToast } from './toasts';
import {detailWorkCaption,relationLabel} from './media-detail-data';
import {detailWorkArtwork} from './detail-artwork';
import {currentDetailSeason,detailSeasonState,type DetailSeason} from './season-detail-data';
import {observeDeferredArtwork} from './visible-artwork';
import {responsiveArtwork} from './responsive-artwork';

let pageListeners: AbortController | undefined;
let mountedRoot: HTMLElement | null = null;
export function mountMediaDetail() {
  const root = document.querySelector<HTMLElement>('[data-media-detail]');
  // The initial module and Astro's page-load event can both arrive for the
  // same document. Preserve deferred credits, pending reads and loaded images.
  if(root && root === mountedRoot && pageListeners && !pageListeners.signal.aborted)return;
  pageListeners?.abort();
  mountedRoot = root;
  if (!root) return;
  pageListeners = new AbortController();
  const {signal} = pageListeners;
  document.addEventListener('astro:before-swap', () => {pageListeners?.abort();mountedRoot=null;}, {signal,once:true});
  seedEditorPage();
  const info = JSON.parse(root.querySelector('#media-detail-data')!.textContent!);
  const deferArtwork=observeDeferredArtwork(root,signal);
  mountDetailCast(root,signal,info);
  if(info.backgroundAvailable){
    const scoreStatus=root.querySelector<HTMLElement>('[data-detail-score-status]');
    const scores=detailBackground<Record<string,any>>(`/api/proxy/tracking/title/${info.id}/scores`,signal,data=>{
      root.querySelectorAll<HTMLElement>('[data-detail-score]').forEach(node=>{
        const field=node.dataset.detailScore!;
        if(!(field in data))return;
        const value=data[field];node.classList.remove('detail-value-skeleton');
        node.textContent=value==null||value===0?'—':field.startsWith('rt_')?`${Math.round(value)}%`:String(Number(value.toFixed(1)));
      });
      return data.score_status as DetailLoadState;
    },(state,error)=>{
      if(!scoreStatus)return;
      scoreStatus.hidden=state!=='retry';
      if(state==='retry'){
        scoreStatus.replaceChildren(document.createTextNode(error||'Extra ratings are temporarily unavailable. Retrying automatically.'));
        const button=document.createElement('button');button.type='button';button.className='lab-text-button';button.textContent='Retry';button.addEventListener('click',()=>void scores.retry(),{signal});scoreStatus.append(' ',button);
      }
    });
    scores.start();
  }
  if(info.relatedPending){
    const icons=root.querySelector<HTMLTemplateElement>('[data-detail-work-icons]')!.content;
    const related=detailBackground<any>(`/api/proxy/tracking/title/${info.id}/related`,signal,data=>{
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
          if(work.poster){
            const image=document.createElement('img');image.alt='';image.loading='lazy';image.fetchPriority='low';
            const art=detailWorkArtwork(work.poster,field==='similar');
            if(art){if(art.srcset)image.srcset=art.srcset;if(art.sizes)image.sizes=art.sizes;image.src=art.src;}
            card.append(image);
          }
          else card.append(icons.firstElementChild!.cloneNode(true));
          const copy=document.createElement('div');
          if(field==='relations'){const label=document.createElement('span');label.className='lab-relation-type';label.textContent=work.label||relationLabel(work.relation,work.reverse);copy.append(label);}
          const title=document.createElement('h3');title.textContent=work.title;
          const detail=document.createElement('p');detail.textContent=detailWorkCaption(work,field==='similar');
          copy.append(title,detail);card.append(copy);
          if(field==='similar')card.append(icons.lastElementChild!.cloneNode(true));
          return card;
        }).filter((card:HTMLAnchorElement|null):card is HTMLAnchorElement=>!!card);
        grid.replaceChildren(...cards);section.hidden=!cards.length;
        if(cards.length&&section.getBoundingClientRect().top<innerHeight&&section.getBoundingClientRect().bottom>0)reveal(section);
      }
      return data.related_status;
    },(state,error)=>{
      if(state==='retry')showToast({message:error||'Related titles are temporarily unavailable. Retrying automatically.',kind:'error',key:'detail-related'});
    });
    related.start();
  }
  let title = editorStore.find(info.id);
  const synopsis = root.querySelector<HTMLElement>('[data-detail-synopsis]');
  if (synopsis) mountContributorBiography(synopsis,signal);
  root.querySelectorAll<HTMLElement>('[data-credit-section]').forEach(section=>{
    const button=section.querySelector<HTMLButtonElement>('[data-credit-toggle]');
    const content=section.querySelector<HTMLElement>('[data-credit-content]');
    const rest=section.querySelector<HTMLElement>('[data-credit-rest]');
    if(!button||!content||!rest)return;
    const reduced=matchMedia('(prefers-reduced-motion: reduce)');
    let expanded=false,animation:Animation|undefined;
    const settle=()=>{animation?.cancel();animation=undefined;rest.hidden=!expanded;content.style.removeProperty('overflow');};
    button.addEventListener('click',()=>{
      const start=content.getBoundingClientRect().height;
      animation?.cancel();expanded=!expanded;
      if(!expanded&&section.getBoundingClientRect().top<0)section.scrollIntoView({block:'start',behavior:reduced.matches?'auto':'smooth'});
      button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'Hide':'Show more';
      rest.hidden=!expanded;
      const end=content.getBoundingClientRect().height;
      if(reduced.matches||start===end){settle();return;}
      rest.hidden=false;content.style.overflow='hidden';
      const current=content.animate([{height:`${start}px`},{height:`${end}px`}],{duration:300,easing:'cubic-bezier(.2,.7,.2,1)',fill:'both'});
      animation=current;current.finished.then(()=>{if(animation===current)settle();}).catch(()=>{});
    },{signal});
    reduced.addEventListener('change',settle,{signal});signal.addEventListener('abort',settle,{once:true});
  });
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!signal.aborted) initializeScrollMotion(root,signal,'.lab-module','media-scroll-reveal', {
      animateInitial:false,downwardOnly:true,once:true,
    });
  }));

  let loadDetailImages=()=>Promise.resolve();
  let episodeImagesPending:Promise<void>|undefined;
  let episodeImagesLoaded=false;
  const loadEpisodeImages=()=>{
    if(info.type!=='series'||episodeImagesLoaded||episodeImagesPending)return;
    episodeImagesPending=(async()=>{
      const response=await fetch(`/api/proxy/tracking/title/${info.id}/episode-images`,{signal});
      if(!response.ok)throw new Error('Episode artwork is temporarily unavailable.');
      const data=await response.json();
      if(signal.aborted)return;
      for(const [id,url] of Object.entries(data.images||{})){
        const row=root.querySelector<HTMLElement>(`[data-episode-row="${id}"]`);
        const holder=row?.querySelector<HTMLElement>('.lab-episode-placeholder');
        const art=responsiveArtwork(String(url),{size:'w300',sizes:'(max-width:650px) 55px, 125px',route:'direct'});
        if(!holder||!art)continue;
        const image=document.createElement('img');image.alt='';image.loading='lazy';image.fetchPriority='low';
        image.dataset.deferredSrc=art.src;
        if(art.srcset)image.dataset.deferredSrcset=art.srcset;
        if(art.sizes)image.dataset.deferredSizes=art.sizes;
        holder.classList.remove('lab-episode-placeholder');holder.removeAttribute('aria-hidden');holder.replaceChildren(image);
        deferArtwork(image);
      }
      episodeImagesLoaded=true;
    })().catch(error=>{if(!signal.aborted)console.debug('Episode artwork deferred.',error);}).finally(()=>{episodeImagesPending=undefined;});
  };
  let socialPending:Promise<void>|undefined;
  let socialLoaded=false;
  const loadSocial=()=>{
    const host=root.querySelector<HTMLElement>('[data-detail-social]');
    if(!host||socialLoaded||socialPending)return;
    socialPending=(async()=>{
      const response=await fetch(`/title/${info.id}/social`,{signal});
      if(!response.ok)throw new Error('Unable to load social activity.');
      const html=new DOMParser().parseFromString(await response.text(),'text/html');
      const content=html.querySelector('.media-social-grid');
      if(!content)throw new Error('Unable to load social activity.');
      if(signal.aborted)return;
      host.replaceChildren(document.importNode(content,true));socialLoaded=true;
    })().catch(error=>{if(!signal.aborted)host.textContent=error instanceof Error?error.message:'Unable to load social activity.';}).finally(()=>{socialPending=undefined;});
  };
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
    if(id==='artwork')void loadDetailImages();
    if(id==='seasons')loadEpisodeImages();
    if(id==='social')loadSocial();
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
  let noteDirty = false;
  note?.addEventListener('input',()=>{noteDirty=true;},{signal});
  const paint = (state: EditorTitle) => {
    title = state;
    const catalogue=(state as EditorTitle & {seasons?:DetailSeason[]}).seasons||[];
    const watching=state.entry?.status==='watching';
    const current=currentDetailSeason(catalogue,watching);
    for(const season of catalogue){
      const value=detailSeasonState(season,current,watching);
      const indicator=root.querySelector<HTMLElement>(`[data-season-state="${season.season_number}"]`);
      if(!indicator)continue;
      const label=indicator.querySelector<HTMLElement>('[data-season-state-label]')!;
      if(label.textContent!==value.label){label.textContent=value.label;reveal(indicator);}
      indicator.dataset.watched=String(value.watched);
    }
    if (favorite) {
      const active = state.favorite ?? !!state.entry?.favorite;
      favorite.dataset.favorite=String(active); favorite.setAttribute('aria-pressed',String(active));
      favorite.title=active?'Remove from favorites':'Add to favorites'; favorite.setAttribute('aria-label',favorite.title);
    }
    if (library) {
      const active = !!state.editor_library?.desired;
      library.dataset.desired=String(active); library.setAttribute('aria-pressed',String(active));
      library.title=active?'Remove from library':'Add to library'; library.setAttribute('aria-label',library.title);
    }
    if (note && !noteDirty) note.value=state.entry?.notes || '';
    root.querySelectorAll<HTMLElement>('[data-season-score]').forEach(score=>score.textContent=String(state.entry?.season_scores?.[score.dataset.seasonScore!] || '—'));
    const average=root.querySelector<HTMLInputElement>('[data-season-average]');
    if(average)average.checked=state.entry?.rating_mode==='average';
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
    const desired=!(title.favorite ?? title.entry?.favorite);
    void editorStore.write(title,{favorite:desired}).then(saved=>{
      if (title?.favorite===saved.favorite) showToast({message:desired?'Added to favorites.':'Removed from favorites.',kind:'success',key:'detail-favorite'});
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

  loadDetailImages=mountDetailImages(root,info,signal);

  const seasons=root.querySelector<HTMLElement>('.seasons-section');
  seasons?.querySelectorAll<HTMLDetailsElement>('[data-season-disclosure]').forEach(details=>{
    const trigger=details.closest('[data-season-card]')?.querySelector<HTMLButtonElement>('[data-season-toggle]');
    mountDetailsMotion(details,signal,trigger?[trigger]:[]);
  });
  const paintEpisodeTitles=()=>{
    root.querySelectorAll<HTMLElement>('[data-episode-row]').forEach(row=>{
      const heading=row.querySelector<HTMLElement>('[data-episode-title]')!;
      heading.textContent=root.dataset.spoilers==='true'||row.dataset.watched==='true'?heading.dataset.episodeTitle!:`Episode ${heading.dataset.episodeNumber}`;
    });
  };
  root.querySelector<HTMLButtonElement>('[data-episode-spoilers]')?.addEventListener('click',event=>{
    const button=event.currentTarget as HTMLButtonElement;
    const showing=root.dataset.spoilers!=='true';root.dataset.spoilers=String(showing);
    button.setAttribute('aria-pressed',String(showing));button.querySelector('[data-episode-spoilers-label]')!.textContent=showing?'Titles shown':'Titles hidden';
    paintEpisodeTitles();
  },{signal});
  root.querySelector<HTMLInputElement>('[data-season-average]')?.addEventListener('change',async event=>{
    const input=event.currentTarget as HTMLInputElement;if(!title)return;
    input.disabled=true;
    try{await editorStore.write(title,{rating_mode:input.checked?'average':'manual'});}
    catch(error){if(!signal.aborted)showToast((error as Error).message,'error');}
    finally{input.disabled=false;}
  },{signal});
  let seasonReadVersion=0;
  const refreshSeasonState=async()=>{
    const version=++seasonReadVersion;
    const response=await fetch(`/api/proxy/tracking/title/${info.id}`,{signal});
    if(!response.ok)throw new Error('Unable to refresh episode state. Reload to see your saved change.');
    const data=await response.json();if(signal.aborted||version!==seasonReadVersion)return;
    paint(editorStore.seed({...data,editor_library:title?.editor_library}));
    for(const season of data.seasons||[]){
      const number=season.season_number;
      for(const episode of season.episodes||[]){
        const row=root.querySelector<HTMLElement>(`[data-episode-row="${episode.id}"]`);if(!row)continue;
        row.dataset.watched=String(episode.watched);
        const button=row.querySelector<HTMLButtonElement>('[data-watch-episode]');
        if(button){button.setAttribute('aria-pressed',String(episode.watched));button.setAttribute('aria-label',`Mark season ${number} episode ${episode.episode_number} ${episode.watched?'unwatched':'watched'}`);button.disabled=!episode.released||season.released_count==null;}
      }
    }
    paintEpisodeTitles();
  };
  document.addEventListener('anylist:editor-committed',event=>{
    const {title:saved,patch}=(event as CustomEvent<{title:EditorTitle;patch:Record<string,unknown>|null}>).detail;
    if(saved.id!==info.id||info.type!=='series'||(patch&&!['status','progress','rewatch_count'].some(key=>key in patch)))return;
    void refreshSeasonState().catch(()=>{if(!signal.aborted)showToast('Reload to see your updated episode state.','error');});
  },{signal});
  root.querySelectorAll<HTMLButtonElement>('[data-watch-episode]').forEach(button=>button.addEventListener('click',async()=>{
    const watched=button.getAttribute('aria-pressed')!=='true';button.disabled=true;button.setAttribute('aria-busy','true');
    try{
      const id=Number(button.dataset.watchEpisode);
      const response=await fetch(`/api/proxy/tracking/entry/${info.id}/episode/${id}`,{signal,method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({watched})});
      const result=await response.json().catch(()=>({}));if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Unable to update this episode.');
      await refreshSeasonState();
    }catch(error){if(!signal.aborted)showToast((error as Error).message,'error');}
    finally{button.disabled=false;button.removeAttribute('aria-busy');}
  },{signal}));
  const request=async(path:string)=>{
    const response=await fetch(`/api/proxy/tracking/${path}`,{signal,method:'POST'});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Unable to refresh episode metadata.');
    return data;
  };
  root.querySelector<HTMLButtonElement>('#refresh-episodes')?.addEventListener('click',async event=>{
    const button=event.currentTarget as HTMLButtonElement;button.disabled=true;button.setAttribute('aria-busy','true');
    try{await request(`title/${info.id}/refresh-episodes`);location.reload();}
    catch(error){showToast((error as Error).message,'error');button.disabled=false;button.removeAttribute('aria-busy');}
  },{signal});
  const mode=root.querySelector<HTMLDialogElement>('#season-mode');
  mode?.addEventListener('click',event=>{if(event.target===mode)void closeDialog(mode,'cancel');},{signal});
  root.querySelectorAll<HTMLButtonElement>('[data-open-season-score]').forEach(button=>button.addEventListener('click',()=>{
    if(!title)return;
    const open=(window as Window & {anyListQuickRate?:(detail:{title:string;poster?:string;score:number|null;ratingMode:'manual';onScore:(score:number|null)=>Promise<void>})=>void}).anyListQuickRate;
    if(!open){showToast('The rating picker is loading. Try again.','error');return;}
    const number=button.dataset.openSeasonScore!;
    open({title:`${title.title} · Season ${number}`,poster:title.poster||undefined,
      score:title.entry?.season_scores?.[number]||null,ratingMode:'manual',onScore:async score=>{
        if(!title||signal.aborted)return;
        button.disabled=true;
        try {
          const patch:Record<string,unknown>={season_scores:{[number]:score??0}};
          const hasSeasons=Object.values(title.entry?.season_scores||{}).some(Boolean);
          if(!hasSeasons&&title.entry?.rating_mode==='average')patch.rating_mode='average';
          if(!hasSeasons&&title.entry?.rating_mode!=='average'&&score&&mode){
            const picker=document.querySelector<HTMLDialogElement>('#quick-rating');
            if(picker?.open)await new Promise<void>(resolve=>{
              const done=()=>{picker.removeEventListener('close',done);signal.removeEventListener('abort',done);resolve();};
              picker.addEventListener('close',done,{once:true});signal.addEventListener('abort',done,{once:true});
            });
            if(signal.aborted)return;
            mode.returnValue='cancel';showDialog(mode);
            const result=await new Promise<string>(resolve=>{
              const done=()=>{mode.removeEventListener('close',done);signal.removeEventListener('abort',done);resolve(signal.aborted?'cancel':mode.returnValue);};
              mode.addEventListener('close',done,{once:true});signal.addEventListener('abort',done,{once:true});
            });
            if(!['manual','average'].includes(result)||signal.aborted)return;
            patch.rating_mode=result;
          }
          await editorStore.write(title,patch);
          if(!signal.aborted)showToast(score==null?'Season score removed.':'Season score saved.','success');
        }finally{button.disabled=false;}
      }});
  },{signal}));
  if(new URLSearchParams(location.search).get('rate')==='1')requestAnimationFrame(()=>{if(!signal.aborted)root.querySelector<HTMLButtonElement>('[data-quick-rate-id]')?.click();});
}
