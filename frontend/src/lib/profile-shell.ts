import {statisticsDomainUrl} from './statistics-domains';
import {preparePanelStyles,panelLayoutFrame} from './panel-readiness';
import {isProfileList,isProfileSummary,profilePanelKey,profileRefreshAge,setPanelWaiting} from './profile-panel';
import {bindPredictionIntent,predictionAllowed,predictionManifest} from './predictive-loading';
import {profileLoadingFrame} from './profile-loading';
import {settleNavigationContent,cancelLoadingReveals} from './loading-reveal';
import {startVisibleRefresh} from './visible-refresh';
import {editorStore} from './editor-store';
import {returnViews,returnViewIdentity,returnViewKey,type CaptureReturnView} from './return-view-cache';
import {profileSessionCache} from './profile-session-cache';
import {profilePanelHeader} from './profile-panel-request';

const summaryBudget = 512 * 1024;
type Panel={root:HTMLElement;url:string;title:string;ready:boolean;kind:string;lifetime:AbortController;styles?:DocumentFragment;initializing?:Promise<void>;preparing?:Promise<void>;summary?:string;checkedAt:number;visibilityCheckedAt:number;revision:number;saved?:boolean};
let session:AbortController|undefined;
let currentHost:HTMLElement|undefined;
const summaryHtml=(panel:Panel)=>panel.root.querySelector('[data-profile-tab-content]')?.outerHTML;
const parseSeed=(html:string)=>{
  const template=document.createElement('template');template.innerHTML=html;
  return template.content;
};

function panelInitializer(kind:string){
  switch(kind){
    case 'overview': return Promise.resolve(undefined);
    case 'favorites': return import('./profile-favorites-panel').then(module=>module.initializeFavorites);
    case 'social': return import('./profile-social-panel').then(module=>module.initializeSocial);
    case 'library': return import('./profile-library-panel').then(module=>module.initializeLibrary);
    case 'stats':
    case 'stats/games': return import('./stats-overview').then(module=>module.initializeStatistics);
    case 'games': return import('./game-list').then(module=>module.initializeGameList);
    default: return import('./profile-list').then(module=>module.initializeProfileList);
  }
}

async function initialize(panel:Panel,signal:AbortSignal,styles?:Promise<void>,component=panelInitializer(panel.kind)){
  if(panel.ready)return;
  if(panel.initializing)return panel.initializing;
  const pending=(async()=>{
    const statistics=panel.root.querySelector<HTMLElement>('[data-integrated-statistics]');
    const [loaded,layout]=await Promise.allSettled([component,styles]);
    if(loaded.status==='rejected')throw loaded.reason;
    if(layout.status==='rejected')throw layout.reason;
    if(signal.aborted||panel.lifetime.signal.aborted||!panel.root.isConnected)return;
    loaded.value?.(statistics??panel.root,AbortSignal.any([signal,panel.lifetime.signal]));panel.ready=true;
  })().finally(()=>{panel.initializing=undefined;});
  panel.initializing=pending;return pending;
}

function setup(){
  const route=profilePanelKey(new URL(location.href)),host=document.querySelector<HTMLElement>('#content');
  if(!route||!host||currentHost===host||!host.querySelector('.profile-tabs')||host.querySelector('[data-profile-shell]'))return;
  session?.abort();const controller=session=new AbortController(),{signal}=controller;
  currentHost=host;host.dataset.profileShell='';
  const viewer=document.body.dataset.cacheUser,approved=document.body.dataset.gamesApproved;
  const header=host.querySelector<HTMLElement>('.profile-hero')!,nav=host.querySelector<HTMLElement>('.profile-tabs')!;
  const owner=nav.dataset.profileOwner==='yes';
  const savedPanels=owner&&viewer&&viewer!=='anonymous'?profileSessionCache(returnViewIdentity(document.body)):undefined;
  const restored=document.body.hasAttribute('data-return-view');
  const restoredDirty=document.body.dataset.returnView==='dirty';
  const visibilityPath=`/api/proxy/tracking/people/${encodeURIComponent(route.username)}/header`;
  let generation=0;
  let summaryRevision=0;
  const panels=new Map<string,Panel>(),flights=new Map<string,Promise<Panel>>();
  const forget=(panel:Panel)=>{panel.lifetime.abort();for(const [key,value] of panels)if(value===panel)panels.delete(key);};
  signal.addEventListener('abort',()=>{panels.clear();flights.clear();},{once:true});
  const first=document.createElement('div');first.dataset.profileUrl=location.href;
  if(restored){
    first.dataset.profileStatsCheckedAt=restoredDirty?'0':document.body.dataset.profileReturnCheckedAt||'0';
    if(Date.now()-Number(first.dataset.profileStatsCheckedAt)>=profileRefreshAge)first.dataset.returnListDirty='';
  }
  for(const child of [...host.childNodes])if(child!==header&&child!==nav)first.append(child);
  const stage=document.createElement('div');stage.dataset.profilePanels='';host.append(stage);stage.append(first);

  let active=route.key,wanted=active,shown:HTMLElement=first;
  let selection=0;
  let initialReady=false,leaving=false;
  const attempted=new Set<string>();
  let warmFrame=0,warmTimer=0,warming=false;
  let warmRequest:{key:string;controller:AbortController}|undefined;
  const createPanel=(root:HTMLElement,url:string,title:string,kind:string,styles?:DocumentFragment):Panel=>{
    const now=Date.now();
    const panel:Panel={root,url,title,kind,styles,ready:false,lifetime:new AbortController(),checkedAt:now,visibilityCheckedAt:now,revision:summaryRevision};
    if(isProfileSummary(kind)){
      const html=summaryHtml(panel);
      if(html&&new Blob([html]).size<=summaryBudget)panel.summary=html;
    }
    return panel;
  };
  const initial=createPanel(first,location.href,document.title,active);panels.set(active,initial);
  if(first.querySelector('[data-integrated-statistics]')){panels.set('stats',initial);panels.set('stats/games',initial);}
  function update(){
    for(const link of nav.querySelectorAll<HTMLAnchorElement>('a[href]')){
      const match=profilePanelKey(new URL(link.href));if(!match)continue;
      const selected=match.key===wanted||match.key==='stats'&&wanted==='stats/games';
      link.classList.toggle('active',selected);if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    }
  }
  function pruneLists(keep:Panel){
    const hasCombined=!!nav.querySelector('a[href$="/list"]');
    for(const [key,panel] of panels){
      if(panel===keep||!isProfileList(key)||key==='games')continue;
      if(hasCombined?key!=='list':key==='list')forget(panel);
    }
  }
  async function readPage(url:string,readSignal:AbortSignal,background=false){
    const response=await fetch(url,{signal:AbortSignal.any([readSignal,AbortSignal.timeout(15_000)]),credentials:'same-origin',cache:'no-store',priority:background?'low':'auto',headers:{Accept:'text/html',[profilePanelHeader]:'1'}});
    if([401,403,404].includes(response.status)||response.redirected)throw new ProfileAccessChanged();
    if(!response.ok)throw new Error('Unable to load this section.');
    const html=await response.text();
    if(!predictionManifest(html,viewer||'anonymous',approved==='true'))throw new ProfileAccessChanged();
    const doc=new DOMParser().parseFromString(html,'text/html');
    if(doc.body.dataset.cacheUser!==viewer||doc.body.dataset.gamesApproved!==approved)throw new ProfileAccessChanged();
    const content=doc.querySelector('#content'),tabs=content?.querySelector<HTMLElement>('.profile-tabs');
    if(!content||!tabs||tabs.dataset.profileBase!==nav.dataset.profileBase)throw new ProfileAccessChanged();
    return {doc,content};
  }
  async function read(key:string,url:string,background?:AbortSignal):Promise<Panel>{
    const lookup=key.startsWith('stats')?'stats':key;
    const existing=panels.get(lookup);if(existing)return existing;
    const flight=flights.get(lookup);if(flight)return flight;
    const revision=generation;
    const dataRevision=summaryRevision;
    const pending:Promise<Panel>=(async()=>{
      const saved=owner?returnViews.get(returnViewKey(new URL(url))):undefined;
      const savedDoc=saved?new DOMParser().parseFromString(saved.html,'text/html'):undefined;
      const savedContent=savedDoc?.querySelector('#content');
      const savedTabs=savedContent?.querySelector<HTMLElement>('.profile-tabs');
      const restore=savedDoc&&savedContent&&savedTabs&&savedTabs.dataset.profileBase===nav.dataset.profileBase&&
        returnViewIdentity(savedDoc.body)===returnViewIdentity(document.body);
      const seed=restore?undefined:savedPanels?.get(returnViewKey(new URL(url)));
      // Parse compact snapshots inside inert templates so images don't load
      // while rebuilding the offscreen panel.
      const {doc,content}=restore?{doc:savedDoc,content:savedContent}:seed?{
        doc:parseSeed(seed.styles),content:parseSeed(seed.content),
      }:await readPage(url,background?AbortSignal.any([signal,background]):signal,!!background);
      if(signal.aborted||revision!==generation)throw new DOMException('Preview invalidated','AbortError');
      const root=document.createElement('div');root.dataset.profileUrl=url;
      for(const child of [...content.childNodes])if(!(child instanceof Element)||!child.matches('.profile-hero,.profile-tabs,script:not([type="application/json"])'))root.append(child);
      const styles=document.createDocumentFragment();
      for(const style of doc.querySelectorAll('style,link[rel=stylesheet][href]'))if(!style.closest('noscript'))styles.append(style);
      const panel=createPanel(root,url,seed?.title??(doc as Document).title,key,styles);
      panel.revision=dataRevision;
      if(seed){panel.checkedAt=seed.checkedAt;panel.saved=true;}
      if(restore){
        panel.checkedAt=saved?.dirty?0:Number(savedDoc.body.dataset.profileReturnCheckedAt)||panel.checkedAt;
        if(saved?.dirty||Date.now()-panel.checkedAt>=profileRefreshAge)root.dataset.returnListDirty='';
      }
      if(seed&&Date.now()-panel.checkedAt>=profileRefreshAge)root.dataset.returnListDirty='';
      if(seed||restore)root.dataset.profileStatsCheckedAt=String(panel.checkedAt);
      if(!isProfileSummary(key)||panel.summary!==undefined||wanted===key)panels.set(key,panel);
      if(root.querySelector('[data-integrated-statistics]')){panels.set('stats',panel);panels.set('stats/games',panel);}
      return panel;
    })().finally(()=>flights.delete(lookup)).catch(error=>{
      if(!signal.aborted&&wanted===key&&error instanceof DOMException&&error.name==='AbortError')return read(key,url);
      throw error;
    });
    flights.set(lookup,pending);return pending;
  }
  async function checkVisibility(){
    const response=await fetch(visibilityPath,{
      signal:AbortSignal.any([signal,AbortSignal.timeout(5_000)]),credentials:'same-origin',cache:'no-store',
    });
    if([401,403,404].includes(response.status)||response.redirected)throw new ProfileAccessChanged();
    if(!response.ok)throw new Error('Unable to check profile visibility.');
  }
  let refreshFlight:AbortController|undefined;
  function invalidateSummaries(){
    summaryRevision++;
    refreshFlight?.abort();refreshFlight=undefined;
    if(isProfileSummary(active))scheduleRefresh();
  }
  let refreshFrame=0;
  function scheduleRefresh(){
    if(!refreshFrame)refreshFrame=requestAnimationFrame(()=>{refreshFrame=0;void refresh();});
  }
  signal.addEventListener('abort',()=>cancelAnimationFrame(refreshFrame),{once:true});
  const canRefreshSummary=(panel:Panel)=>!editorStore.hasPendingWrites()&&
    !panel.root.querySelector('[data-profile-writing],[data-reordering="true"],[data-social-panel="search"]:not([hidden])')&&
    !panel.root.contains(document.activeElement);
  async function refresh(){
    if(signal.aborted)return;
    const panel=panels.get(active);
    if(!panel?.ready||wanted!==active||panel.root!==shown||document.hidden||!navigator.onLine)return;
    if(isProfileList(active)||owner&&active.startsWith('stats')){
      panel.root.dispatchEvent(new Event('anylist:panel-revalidate'));return;
    }
    const canUpdate=isProfileSummary(active)&&canRefreshSummary(panel);
    const needsContent=panel.revision!==summaryRevision||Date.now()-panel.checkedAt>=profileRefreshAge;
    if(!owner&&(!canUpdate||!needsContent)&&Date.now()-panel.visibilityCheckedAt>=profileRefreshAge){
      panel.visibilityCheckedAt=Date.now();
      try{await checkVisibility();}
      catch(error){if(!signal.aborted&&panel.root===shown&&error instanceof ProfileAccessChanged)location.replace(location.href);}
      return;
    }
    if(!canUpdate||!needsContent||refreshFlight)return;
    const request=refreshFlight=new AbortController(),revision=summaryRevision;
    const readSignal=AbortSignal.any([signal,request.signal]);
    try{
      const {content}=await readPage(panel.url,readSignal);
      panel.visibilityCheckedAt=Date.now();
      const next=content.querySelector<HTMLElement>('[data-profile-tab-content]');
      if(!next||readSignal.aborted||revision!==summaryRevision||!canRefreshSummary(panel))return;
      const html=next.outerHTML;
      if(html===panel.summary){panel.checkedAt=Date.now();panel.revision=revision;panel.saved=false;queueWarm();return;}
      const init=await panelInitializer(panel.kind);
      if(readSignal.aborted||revision!==summaryRevision||!canRefreshSummary(panel))return;
      const previous=panel.root.querySelector<HTMLElement>('[data-profile-tab-content]')!;
      if(panel.kind==='favorites')next.dataset.favoritesVisible=JSON.stringify(Object.fromEntries(
        [...previous.querySelectorAll<HTMLElement>('[data-favorites-group]')].map(group=>[
          group.dataset.favoritesGroup,group.querySelector('.favorites-grid')!.childElementCount,
        ])));
      if(panel.kind==='social'){
        const selected=previous.querySelector<HTMLElement>('[data-social-panel]:not([hidden])')?.dataset.socialPanel;
        if(selected){
          next.querySelectorAll<HTMLElement>('[data-social-panel]').forEach(section=>{section.hidden=section.dataset.socialPanel!==selected;});
          next.querySelectorAll<HTMLElement>('.profile-section-selector [data-section]').forEach(button=>{
            const active=button.dataset.section===selected;
            button.classList.toggle('is-selected',active);button.setAttribute('aria-pressed',String(active));
          });
        }
      }
      const top=scrollY;
      panel.lifetime.abort();panel.lifetime=new AbortController();
      previous.replaceWith(next);
      panel.summary=new Blob([html]).size<=summaryBudget?html:undefined;
      init?.(panel.root,AbortSignal.any([signal,panel.lifetime.signal]));
      panel.checkedAt=Date.now();panel.revision=revision;
      panel.saved=false;queueWarm();
      window.scrollTo(0,top);
    }catch(error){
      if(!readSignal.aborted){
        if(error instanceof ProfileAccessChanged)location.replace(panel.url);
        panel.checkedAt=Date.now();panel.revision=revision;
      }
    }finally{
      if(refreshFlight===request)refreshFlight=undefined;
    }
  }
  if(!owner){
    const visibleRefresh=startVisibleRefresh({intervalMs:30_000,wakeIntervalMs:30_000,refresh});
    signal.addEventListener('abort',visibleRefresh.stop,{once:true});
  }else{
    window.addEventListener('focus',scheduleRefresh,{signal});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)scheduleRefresh();},{signal});
  }
  document.addEventListener('anylist:cache-invalidated',()=>{
    generation++;
    attempted.clear();
    for(const panel of new Set(panels.values()))if(!panel.ready&&!panel.initializing&&!panel.preparing&&panel.root!==shown)forget(panel);
    invalidateSummaries();
  },{signal});
  document.addEventListener('anylist:editor-committed',invalidateSummaries,{signal});
  stage.addEventListener('anylist:profile-refresh',()=>void refresh(),{signal});
  stage.addEventListener('anylist:profile-access-changed',()=>location.replace(location.href),{signal});
  function show(root:HTMLElement){cancelLoadingReveals(shown);shown.remove();shown=root;stage.append(root);}
  async function prepare(panel:Panel){
    if(panel.preparing)return panel.preparing;
    if(panel.ready)return;
    if(panel.root===shown)return initialize(panel,signal);
    panel.preparing=(async()=>{
      const root=panel.root;
      try{
        const layoutReady=(async()=>{
          if(panel.styles){
            await preparePanelStyles(panel.styles,panel.url,signal);
            panel.styles=undefined;
          }
          if(signal.aborted||panel.lifetime.signal.aborted)return;
          root.dataset.profilePreparing='';root.inert=true;
          root.style.cssText='position:absolute;left:0;right:0;top:0;visibility:hidden;pointer-events:none';stage.append(root);
          await document.fonts.ready;
        })();
        const [mounted,layout]=await Promise.allSettled([initialize(panel,signal,layoutReady),layoutReady]);
        if(mounted.status==='rejected')throw mounted.reason;
        if(layout.status==='rejected')throw layout.reason;
        await panelLayoutFrame(signal);
      }finally{
        root.remove();root.style.cssText='';delete root.dataset.profilePreparing;root.inert=false;
      }
    })().finally(()=>{panel.preparing=undefined;});
    return panel.preparing;
  }
  let progressRunning=false;
  const stopProgress=()=>{if(progressRunning){progressRunning=false;document.dispatchEvent(new Event('anylist:route-progress-end'));}};
  signal.addEventListener('abort',stopProgress,{once:true});
  function select(key:string,url:string,retry=false,fromTab=false){
    const land = () => {
      const list = isProfileList(key);
      window.scrollTo(0,list?scrollY+nav.getBoundingClientRect().top:0);
      document.dispatchEvent(new CustomEvent('anylist:profile-tab-landed',{detail:{list}}));
    };
    if(key===wanted&&!retry){
      if(key===active&&shown===panels.get(active)?.root)land();
      return;
    }
    const attempt=++selection;
    stopProgress();
    refreshFlight?.abort();refreshFlight=undefined;
    document.dispatchEvent(new Event('anylist:profile-tab-changed'));
    const outgoing=panels.get(active)!;
    if(shown===outgoing.root){
      shown.dataset.profileUrl=location.href;
      shown.dispatchEvent(new Event('anylist:panel-inactive'));
    }
    wanted=key;update();
    host!.querySelector('[data-profile-switch-error]')?.remove();
    const source=outgoing.root;
    nav.setAttribute('aria-busy','true');setPanelWaiting(source,true);
    const cached=panels.get(key);
    let loading:HTMLElement|undefined;
    // Retain usable content while a cached visitor tab awaits its visibility check.
    // Loading frames are only needed when the destination is not prepared yet.
    if (!cached?.ready||cached.preparing) {
      loading = profileLoadingFrame(key, new URL(url, location.href), nav);
      if (loading) {
        loading.dataset.profileUrl = url;
        show(loading);
        land();
      }
    }
    if(loading){progressRunning=true;document.dispatchEvent(new Event('anylist:route-progress-start'));}
    const isCurrent=()=>!signal.aborted&&attempt===selection&&document.body.dataset.cacheUser===viewer;
    const activate=async(panel:Panel)=>{
      if(!isCurrent())return;
      // A cold visitor read goes straight to the authorized page endpoint.
      if(cached&&!owner){
        await checkVisibility();
        if(!isCurrent())return;
        panel.visibilityCheckedAt=Date.now();
      }
      if(!panel.ready||panel.preparing){
        try{await prepare(panel);}
        catch(error){
          if(!isCurrent()||!panel.saved)throw error;
          savedPanels?.delete(returnViewKey(new URL(panel.url)));
          forget(panel);
          return activate(await read(key,url));
        }
      }
      if(!isCurrent())return;
      // A speculative read may have completed before an oversized panel was selected.
      panels.set(key,panel);
      const commit=()=>{
        if(!isCurrent())return;
        const changedPanel=shown!==panel.root;
        stopProgress();setPanelWaiting(source,false);show(panel.root);active=key;document.body.toggleAttribute('data-list-landing',isProfileList(key));
        if(outgoing!==panel&&isProfileSummary(outgoing.kind)&&outgoing.summary===undefined)forget(outgoing);
        pruneLists(panel);
        history.replaceState(history.state,'',panel.root.dataset.profileUrl||panel.url);
        document.title=panel.title;
        const statistics=panel.root.querySelector<HTMLElement>('[data-integrated-statistics]');
        if(statistics&&(!cached?.ready||!fromTab)&&panel.root.dataset.profileUrl!==url)statistics.dispatchEvent(new CustomEvent('anylist:statistics-open',{detail:{url}}));
        nav.removeAttribute('aria-busy');update();
        if(!loading)land();
        if (changedPanel&&loading&&!cached?.ready) {
          const rows = Array.from(panel.root.querySelectorAll<HTMLElement>('.entry-table [data-list-window]'));
          if (rows.length) rows.forEach(row => settleNavigationContent(row,'tab',signal,4));
          else settleNavigationContent(panel.root,'tab',signal);
        }
      };
      commit();
      if(isCurrent()&&shown===panel.root)panel.root.dispatchEvent(new Event('anylist:panel-active'));
      if(cached?.ready||Date.now()-panel.checkedAt>=profileRefreshAge||(isProfileSummary(key)&&panel.revision!==summaryRevision))scheduleRefresh();
      queueWarm();
    };
    const failed=(cause?:unknown)=>{
      if(!isCurrent())return;
      if(cause instanceof ProfileAccessChanged){location.assign(url);return;}
      stopProgress();setPanelWaiting(source,false);nav.removeAttribute('aria-busy');update();
      const error=document.createElement('div');error.dataset.profileSwitchError='';error.className='page-width error-message';error.setAttribute('role','alert');
      const retry=document.createElement('button');retry.className='button-secondary';retry.textContent='Try again';
      retry.addEventListener('click',()=>select(key,url,true,fromTab),{signal});error.append('Unable to load this section. ',retry);shown.prepend(error);
    };
    if(cached?.ready){void activate(cached).catch(failed);return;}
    void read(key,url).then(activate).catch(failed);
  }

  const target=(link:HTMLAnchorElement)=>{
    const tab=link.closest('.profile-tabs')===nav;
    const url=statisticsDomainUrl(new URL(location.href),new URL(link.href)),match=profilePanelKey(url);
    if(url.origin!==location.origin||url.hash||match?.username!==route.username)return;
    if(!tab&&!(match.key.startsWith('stats')&&link.closest('[data-integrated-statistics]')))return;
    if((match.key==='games'||match.key==='stats/games')&&approved!=='true')return;
    if(tab&&match.key.startsWith('stats')){
      const retained=panels.get('stats')?.root.dataset.profileUrl;
      const selected=retained&&profilePanelKey(new URL(retained));
      if(retained&&selected)return {key:selected.key,url:retained};
    }
    return {key:match.key,url:url.href};
  };
  function snapshot(panel:Panel,compact=false){
    if(!panel.ready||panel.preparing||panel.root.querySelector('[data-profile-writing],[data-reordering="true"]'))return;
    const copy=document.createElement('template').content.ownerDocument.importNode(panel.root,true);
    copy.querySelectorAll('[data-token]').forEach(element=>element.removeAttribute('data-token'));
    copy.querySelectorAll('[data-list-row-key]').forEach(row=>row.removeAttribute('data-list-row-key'));
    if(isProfileList(panel.kind)){
      const detail={target:copy,saved:false,compact};
      panel.root.dispatchEvent(new CustomEvent('anylist:capture-list-return',{detail}));
      if(!detail.saved)return;
    }else if(panel.kind.startsWith('stats')){
      const stats=copy.querySelector<HTMLElement>('[data-integrated-statistics]');
      if(!stats||stats.querySelector('.overview-results')?.hasAttribute('hidden')||
        Number(new URL(panel.root.dataset.profileUrl||panel.url).searchParams.get('page')||1)!==1||
        stats.dataset.section!=='overview'||stats.dataset.media!=='all'||
        JSON.parse(stats.dataset.initial||'{}').status!=='ready')return;
    }else if(!panel.summary||panel.kind==='social'&&panel.root.querySelector('[data-social-panel="search"]:not([hidden])'))return;
    copy.querySelectorAll('.virtual-list-error,[data-profile-switch-error]').forEach(error=>error.remove());
    copy.querySelectorAll('dialog[open]').forEach(dialog=>dialog.removeAttribute('open'));
    return copy;
  }
  function savePanel(panel:Panel){
    if(!savedPanels||panel.saved||panel.revision!==summaryRevision||editorStore.hasPendingWrites())return;
    const copy=snapshot(panel,true);
    if(!copy)return;
    const styles=[...document.head.querySelectorAll('link[rel=stylesheet][href],style[data-vite-dev-id],style[data-astro-id]')]
      .map(style=>style.outerHTML).join('');
    const url=new URL(panel.root.dataset.profileUrl||panel.url);
    savedPanels.set(returnViewKey(url),{title:panel.title,content:copy.innerHTML,styles,checkedAt:panel.checkedAt});
    panel.saved=true;
  }
  function queueWarm(){
    if(!owner||!initialReady||warming||warmFrame||warmTimer||leaving||signal.aborted||document.hidden||nav.hasAttribute('aria-busy'))return;
    // Yield a paint before using the main thread, including after a restored tab.
    warmFrame=requestAnimationFrame(()=>{
      warmFrame=0;warmTimer=window.setTimeout(()=>{
        warmTimer=0;void warmNext();
      },0);
    });
  }
  async function warmNext(){
    if(leaving||signal.aborted||document.hidden||nav.hasAttribute('aria-busy'))return;
    const current=panels.get(active);
    if(current)savePanel(current);
    const links=[...nav.querySelectorAll<HTMLAnchorElement>('a[href]')].map(target);
    const missing=links.filter(next=>next&&!attempted.has(next.key)&&!panels.get(next.key)?.ready&&next.key!==active);
    const cached=missing.find(next=>next&&(panels.has(next.key)||savedPanels?.get(returnViewKey(new URL(next.url)))||returnViews.get(returnViewKey(new URL(next.url)))));
    const next=cached||(predictionAllowed()?missing[0]:undefined);
    if(!next){savedPanels?.flush();return;}
    attempted.add(next.key);warming=true;
    const request=new AbortController();
    warmRequest={key:next.key,controller:request};
    try{
      const panel=await read(next.key,next.url,request.signal);
      if(leaving||signal.aborted||document.hidden||nav.hasAttribute('aria-busy')){attempted.delete(next.key);return;}
      if(panels.get(next.key)!==panel)return; // Oversized summaries are loaded on demand.
      try{await prepare(panel);}
      catch(error){
        if(panel.saved){
          savedPanels?.delete(returnViewKey(new URL(panel.url)));
          forget(panel);
        }
        throw error;
      }
      if(!signal.aborted&&!leaving)savePanel(panel);
    }catch {
      if(request.signal.aborted)attempted.delete(next.key);
    }finally{warmRequest=undefined;warming=false;queueWarm();}
  }
  document.addEventListener('astro:before-preparation',()=>{leaving=true;warmRequest?.controller.abort();},{signal});
  document.addEventListener('astro:page-load',()=>{leaving=false;queueWarm();},{signal});
  window.addEventListener('focus',queueWarm,{signal});
  window.addEventListener('online',queueWarm,{signal});
  document.addEventListener('visibilitychange',queueWarm,{signal});
  window.addEventListener('pagehide',()=>savedPanels?.flush(),{signal});
  signal.addEventListener('abort',()=>{cancelAnimationFrame(warmFrame);clearTimeout(warmTimer);},{once:true});
  host.addEventListener('click',event=>{
    const link=(event.target as Element).closest<HTMLAnchorElement>('a[href]');if(!link||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.hasAttribute('download')||link.hasAttribute('data-astro-reload')||link.target)return;
    if(link.closest('[data-integrated-statistics] [data-screen-media]'))return;
    const next=target(link);if(!next)return;
    if(next.key===wanted&&!link.closest('.profile-tabs'))return;
    event.preventDefault();event.stopImmediatePropagation();
    if(warmRequest&&warmRequest.key!==next.key)warmRequest.controller.abort();
    select(next.key,next.url,false,!!link.closest('.profile-tabs'));
  },{signal,capture:true});
  bindPredictionIntent(host,signal,link=>{const next=target(link);if(owner&&next&&next.key!==wanted)void read(next.key,next.url).catch(()=>{});});
  document.addEventListener('anylist:capture-return-view',event=>{
    if(!owner||viewer==='anonymous'||editorStore.hasPendingWrites()||shown!==panels.get(active)?.root)return;
    const {source,save}=(event as CustomEvent<CaptureReturnView>).detail;
    const match=profilePanelKey(source);
    if(match?.username!==route.username)return;
    const activeUrl=match.key===active?source:new URL(shown.dataset.profileUrl||source.href);
    // Save siblings first so the view being left has priority under pressure.
    const candidates=[...new Set(panels.values())].filter(panel=>panel.ready);
    candidates.sort((a,b)=>Number(a.root===shown)-Number(b.root===shown));
    for(const panel of candidates){
      const url=panel.root===shown?activeUrl:new URL(panel.root.dataset.profileUrl||panel.url);
      save(url,(content,body,title)=>{
        const document=content.ownerDocument;
        const copy=snapshot(panel);
        if(!copy)return false;
        const tabs=document.importNode(nav,true);
        tabs.removeAttribute('aria-busy');
        for(const link of tabs.querySelectorAll<HTMLAnchorElement>('a[href]')){
          const linkKey=profilePanelKey(new URL(link.getAttribute('href')!,url))?.key;
          const selected=linkKey===panel.kind||linkKey?.startsWith('stats')&&panel.kind.startsWith('stats');
          link.classList.toggle('active',selected);
          if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
        }
        content.append(document.importNode(header,true),tabs,...copy.childNodes);
        if(title)title.textContent=panel.title;
        body.toggleAttribute('data-list-landing',isProfileList(panel.kind));
        body.dataset.profileReturnCheckedAt=String(panel.checkedAt);
        return true;
      });
    }
    savedPanels?.flush();
  },{signal});
  void initialize(initial,signal).then(()=>{
    initialReady=true;queueWarm();
    if(restored&&!signal.aborted){
      initial.checkedAt=restoredDirty?0:Number(document.body.dataset.profileReturnCheckedAt)||initial.checkedAt;
      scheduleRefresh();
    }
  }).catch(()=>{currentHost=undefined;});
}
class ProfileAccessChanged extends Error {}
document.addEventListener('astro:page-load',setup);
document.addEventListener('astro:before-swap',()=>{session?.abort();currentHost=undefined;});
setup();
