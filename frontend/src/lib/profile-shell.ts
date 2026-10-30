import {statisticsDomainUrl} from './statistics-domains';
import {createContentTransition} from './content-motion';
import {createMinimumDisplay} from './minimum-display';
import {preparePanelStyles,panelLayoutFrame} from './panel-readiness';
import {profilePanelKey,setPanelWaiting} from './profile-panel';
import {bindPredictionIntent,predictionManifest} from './predictive-loading';

type Panel={root:HTMLElement;url:string;title:string;ready:boolean;kind:string;initializing?:Promise<void>;preparing?:Promise<void>};
let session:AbortController|undefined;
let currentHost:HTMLElement|undefined;
async function initialize(panel:Panel,signal:AbortSignal){
  if(panel.ready)return;
  if(panel.initializing)return panel.initializing;
  const pending=(async()=>{
    let mount:(root:HTMLElement,signal:AbortSignal)=>void;
    let root=panel.root;
    if(panel.kind==='games')mount=(await import('./game-list')).initializeGameList;
    else if(panel.kind==='stats'){mount=(await import('./stats-overview')).initializeStatistics;root=panel.root.querySelector('#statistics-overview')!;}
    else if(panel.kind==='stats/games'){mount=(await import('./game-statistics')).initializeGameStatistics;root=panel.root.querySelector('#game-statistics')!;}
    else mount=(await import('./profile-list')).initializeProfileList;
    if(signal.aborted||!panel.root.isConnected)return;
    mount(root,signal);panel.ready=true;
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
  let generation=0;
  const panels=new Map<string,Panel>(),flights=new Map<string,Promise<Panel>>(),positions=new Map<string,number>();
  const invalidate=()=>{generation++;for(const [key,panel] of panels)if(!panel.ready)panels.delete(key);};
  document.addEventListener('anylist:editor-state',invalidate,{signal});
  document.addEventListener('anylist:cache-invalidated',invalidate,{signal});
  const loadingTemplates=new Map([...document.querySelectorAll<HTMLTemplateElement>('[data-profile-statistics-loading]')].map(template=>[template.dataset.profileStatisticsLoading!,template]));
  const first=document.createElement('div');first.dataset.profileUrl=location.href;
  for(const child of [...host.childNodes])if(child!==header&&child!==nav)first.append(child);
  const stage=document.createElement('div');stage.dataset.profilePanels='';host.append(stage);stage.append(first);
  const changeStatistics=createContentTransition(stage,signal);
  let active=route.key,wanted=active,shown:HTMLElement=first;
  const initial:Panel={root:first,url:location.href,title:document.title,ready:false,kind:active};panels.set(active,initial);
  const targets=new Map<string,string>();
  nav.querySelectorAll<HTMLAnchorElement>('a[href]').forEach(link=>{const url=new URL(link.href),match=profilePanelKey(url);if(match?.username===route.username)targets.set(match.key,url.href);});
  const stats=`/user/${encodeURIComponent(route.username)}/stats`;
  targets.set('stats',new URL(stats,location.origin).href);
  if(approved==='true')targets.set('stats/games',new URL(stats+'/games',location.origin).href);
  function update(){
    for(const link of nav.querySelectorAll<HTMLAnchorElement>('a[href]')){
      const match=profilePanelKey(new URL(link.href));if(!match)continue;
      const selected=match.key===wanted||match.key==='stats'&&wanted==='stats/games';
      link.classList.toggle('active',selected);if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
      const panel=panels.get(match.key);if(panel)link.href=panel.root.dataset.profileUrl||panel.url;
    }
  }
  async function read(key:string,url:string):Promise<Panel>{
    const existing=panels.get(key);if(existing)return existing;
    const flight=flights.get(key);if(flight)return flight;
    const revision=generation;
    const pending:Promise<Panel>=(async()=>{
      const response=await fetch(url,{signal,credentials:'same-origin',headers:{Accept:'text/html'}});
      if(!response.ok||response.redirected)throw new Error('Unable to load this section.');
      const html=await response.text();
      if(signal.aborted||!predictionManifest(html,viewer||'anonymous',approved==='true'))throw new Error('Account changed.');
      const doc=new DOMParser().parseFromString(html,'text/html');
      if(doc.body.dataset.cacheUser!==viewer)throw new Error('Account changed.');
      if((key==='games'||key==='stats/games')&&doc.body.dataset.gamesApproved!=='true')throw new Error('Games access requires approval.');
      const content=doc.querySelector('#content');if(!content?.querySelector('.profile-tabs'))throw new Error('Unable to load this section.');
      await preparePanelStyles(doc,url,signal);
      if(signal.aborted||revision!==generation)throw new DOMException('Preview invalidated','AbortError');
      const root=document.createElement('div');root.dataset.profileUrl=url;
      for(const child of [...content.childNodes])if(!(child instanceof Element)||!child.matches('.profile-hero,.profile-tabs'))root.append(child);
      const panel:Panel={root,url,title:doc.title,ready:false,kind:key};panels.set(key,panel);return panel;
    })().finally(()=>flights.delete(key)).catch(error=>{if(!signal.aborted&&error instanceof DOMException&&error.name==='AbortError')return read(key,url);throw error;});
    flights.set(key,pending);return pending;
  }
  function show(root:HTMLElement){shown.remove();shown=root;stage.append(root);}
  let progressRunning=false;
  const stopProgress=()=>{if(progressRunning){progressRunning=false;document.dispatchEvent(new Event('anylist:route-progress-end'));}};
  signal.addEventListener('abort',stopProgress,{once:true});
  function select(key:string,url:string){
    if(key===wanted)return;
    changeStatistics.cancel();
    stopProgress();
    const leavingPreview=shown.matches('[data-profile-stats-preview]');
    if(leavingPreview){const source=panels.get(active);if(source){shown.remove();shown=source.root;stage.append(shown);}}
    document.dispatchEvent(new Event('anylist:profile-tab-changed'));
    if(!leavingPreview&&shown===panels.get(active)?.root){positions.set(active,scrollY);shown.dataset.profileUrl=location.href;shown.dispatchEvent(new Event('anylist:panel-inactive'));}
    wanted=key;update();
    host!.querySelector('[data-profile-switch-error]')?.remove();
    const source=shown;
    const statisticsSwap=active.startsWith('stats')&&key.startsWith('stats')&&active!==key;
    const desiredSection=new URL(url,location.href).searchParams.get('section')||'overview';
    nav.setAttribute('aria-busy','true');setPanelWaiting(source,true);
    const cached=panels.get(key);
    const loadingTemplate=loadingTemplates.get(key);
    const skeletonTiming=createMinimumDisplay(signal);
    let skeletonReady=Promise.resolve();
    if(!cached?.ready&&!loadingTemplate){progressRunning=true;document.dispatchEvent(new Event('anylist:route-progress-start'));}
    if(loadingTemplate&&!cached?.ready){
      const preview=loadingTemplate.content.firstElementChild!.cloneNode(true) as HTMLElement;
      const options=[...preview.querySelectorAll<HTMLButtonElement>('[data-section]')];
      options.forEach(button=>{const selected=button.dataset.section===desiredSection;button.classList.toggle('is-selected',selected);button.setAttribute('aria-pressed',String(selected));});
      const heading=preview.querySelector<HTMLElement>('.overview-heading h2');if(heading)heading.textContent=options.find(button=>button.dataset.section===desiredSection)?.textContent||'Overview';
      preview.querySelector<HTMLElement>('[data-stats-headline-loading]')!.hidden=desiredSection!=='overview';
      show(preview);skeletonTiming.show();
      skeletonReady=skeletonTiming.settle();
      window.scrollTo(0,positions.get(key)??Math.max(0,scrollY+nav.getBoundingClientRect().top));
    }
    const activate=async(panel:Panel)=>{
      if(signal.aborted||wanted!==key||document.body.dataset.cacheUser!==viewer)return;
      if(panel.preparing)await panel.preparing;
      else if(!panel.ready&&panel.root===shown)await initialize(panel,signal);
      else if(!panel.ready){
        // One shared staging task also owns rapid away/back switches.
        panel.preparing=(async()=>{
          const root=panel.root;root.dataset.profilePreparing='';root.inert=true;
          root.style.cssText='position:absolute;left:0;right:0;top:0;visibility:hidden;pointer-events:none';stage.append(root);
          try{
            await document.fonts.ready;
            if(signal.aborted)return;
            await initialize(panel,signal);
            await panelLayoutFrame(signal);await panelLayoutFrame(signal);
          }finally{
            root.remove();root.style.cssText='';delete root.dataset.profilePreparing;root.inert=false;
          }
        })().finally(()=>{panel.preparing=undefined;});
        await panel.preparing;
      }
      if(signal.aborted||wanted!==key)return;
      if(loadingTemplate&&!cached?.ready)await skeletonReady;
      if(signal.aborted||wanted!==key)return;
      const commit=()=>{
        if(signal.aborted||wanted!==key)return;
        if(statisticsSwap){
          const destination=new URL(panel.root.dataset.profileUrl||panel.url,location.href);
          if(desiredSection!==(destination.searchParams.get('section')||'overview'))destination.searchParams.delete('page');
          destination.searchParams.set('section',desiredSection);panel.root.dataset.profileUrl=destination.href;
        }
        stopProgress();setPanelWaiting(source,false);show(panel.root);active=key;document.body.toggleAttribute('data-list-landing',!key.startsWith('stats'));
        history.replaceState(history.state,'',panel.root.dataset.profileUrl||panel.url);document.title=panel.title;
        if(statisticsSwap)panel.root.querySelector<HTMLElement>('#statistics-overview,#game-statistics')?.dispatchEvent(new CustomEvent('anylist:statistics-domain-section',{detail:{section:desiredSection}}));
        nav.removeAttribute('aria-busy');update();
        window.scrollTo(0,positions.get(key)??scrollY);
      };
      if(statisticsSwap)await changeStatistics(commit);else commit();
      if(!signal.aborted&&wanted===key&&shown===panel.root)panel.root.dispatchEvent(new Event('anylist:panel-active'));
    };
    const failed=()=>{
      if(signal.aborted||wanted!==key)return;
      stopProgress();setPanelWaiting(source,false);if(shown!==source)show(source);window.scrollTo(0,positions.get(active)??scrollY);nav.removeAttribute('aria-busy');wanted=active;update();
      const error=document.createElement('div');error.dataset.profileSwitchError='';error.className='page-width error-message';error.setAttribute('role','alert');
      const retry=document.createElement('button');retry.className='button-secondary';retry.textContent='Try again';
      retry.addEventListener('click',()=>select(key,url),{signal});error.append('Unable to load this section. ',retry);host!.append(error);
      shown.dispatchEvent(new Event('anylist:panel-active'));
    };
    if(cached?.ready){void activate(cached).catch(failed);return;}
    void read(key,url).then(activate).catch(failed);
  }

  const target=(link:HTMLAnchorElement)=>{const url=statisticsDomainUrl(new URL(location.href),new URL(link.href)),match=profilePanelKey(url);return url.origin===location.origin&&match?.username===route.username&&targets.has(match.key)?{key:match.key,url:url.href}:undefined;};
  host.addEventListener('click',event=>{
    const link=(event.target as Element).closest<HTMLAnchorElement>('a[href]');if(!link||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.hasAttribute('download')||link.target)return;
    const next=target(link);if(!next)return;
    if(next.key===wanted&&!link.closest('.profile-tabs,.stats-domain-navigation'))return;
    event.preventDefault();event.stopImmediatePropagation();select(next.key,next.url);
  },{signal,capture:true});
  bindPredictionIntent(host,signal,link=>{const next=target(link);if(next&&next.key!==wanted)void read(next.key,next.url).catch(()=>{});});
  void initialize(initial,signal).catch(()=>{currentHost=undefined;});
}
document.addEventListener('astro:page-load',setup);
document.addEventListener('astro:before-swap',()=>{session?.abort();currentHost=undefined;});
setup();
