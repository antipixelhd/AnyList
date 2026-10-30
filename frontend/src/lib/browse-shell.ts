import {preparePanelStyles} from './panel-readiness';
import {initializeBrowse} from './browse';
import {initializeGameBrowse} from './game-browse';
import {bindPredictionIntent,predictionAllowed,predictionManifest} from './predictive-loading';
import {browseTab,createBrowseTabs,scopeBrowseIds,type BrowseTab} from './browse-tabs';

let session:AbortController|undefined;
let mounted:HTMLElement|undefined;

function setup(){
  if(mounted?.isConnected)return;
  session?.abort();
  const initialRoot=document.querySelector<HTMLElement>('[data-browse-page]');if(!initialRoot)return;
  const first:HTMLElement=initialRoot;
  const initial=browseTab(new URL(location.href));if(!initial)return;
  const controller=session=new AbortController(),{signal}=controller,viewer=document.body.dataset.cacheUser;
  const host=mounted=document.createElement('div');host.dataset.browseShell='';first.before(host);host.append(first);
  const nav=first.querySelector<HTMLElement>('.browse-types')!;
  const region=(first.querySelector('[name=region]') as HTMLInputElement)?.value||new URL(location.href).searchParams.get('region');
  const targets:Record<BrowseTab,string>={movie:`/browse${region?`?region=${encodeURIComponent(region)}`:''}`,
    series:`/browse?type=series${region?`&region=${encodeURIComponent(region)}`:''}`,game:'/browse/games'};
  const positions=new Map<BrowseTab,number>();
  let active=initial,wanted=initial,shown=first,loading:HTMLElement|undefined;

  function prepare(root:HTMLElement,tab:BrowseTab,url:string,panelSignal=signal){
    root.dataset.browseTab=tab;root.dataset.browseUrl=url;
    root.addEventListener('anylist:browse-url',()=>updateNav(wanted),{signal:panelSignal});
    scopeBrowseIds(root,tab);
    const oldNav=root.querySelector('.browse-types')!;
    const slot=document.createElement('span');slot.dataset.browseTabSlot='';oldNav.replaceWith(slot);
    if(tab==='game')initializeGameBrowse(root,panelSignal);else initializeBrowse(root,panelSignal);
    return root;
  }
  function updateNav(tab:BrowseTab){
    nav.querySelectorAll<HTMLAnchorElement>('[data-browse-type]').forEach(link=>{
      const type=link.dataset.browseType as BrowseTab;
      link.href=tabs.peek(type)?.dataset.browseUrl||targets[type];
      if(type===tab)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    });
  }
  function display(root:HTMLElement,tab:BrowseTab){
    const focus=nav.contains(document.activeElement)?document.activeElement as HTMLElement:null;
    shown.hidden=true;shown.inert=true;
    root.hidden=false;root.inert=false;shown=root;
    root.querySelector('[data-browse-tab-slot]')!.append(nav);
    updateNav(tab);focus?.focus({preventScroll:true});
  }
  function warmImages(root:HTMLElement,panelSignal:AbortSignal){
    const warmed=new Set<HTMLImageElement>();
    const warm=()=>{
      if(!predictionAllowed())return;
      for(const image of root.querySelectorAll<HTMLImageElement>('[data-poster]')){
        if(warmed.size>=6)break;
        if(warmed.has(image)||!image.getAttribute('src'))continue;
        warmed.add(image);image.loading='eager';void image.decode().catch(()=>{});
      }
    };
    const observer=new MutationObserver(warm);observer.observe(root,{childList:true,subtree:true});warm();
    panelSignal.addEventListener('abort',()=>observer.disconnect(),{once:true});
  }
  const tabs=createBrowseTabs<HTMLElement>(async(tab,requestSignal)=>{
    const response=await fetch(targets[tab],{signal:requestSignal,credentials:'same-origin',headers:{Accept:'text/html'}});
    if(!response.ok||response.redirected)throw new Error('Unable to load this tab.');
    const html=await response.text();
    if(requestSignal.aborted||!predictionManifest(html,viewer||'anonymous',tab==='game'))throw new Error('Account changed.');
    const doc=new DOMParser().parseFromString(html,'text/html');
    if(requestSignal.aborted||doc.body.dataset.cacheUser!==viewer)throw new Error('Account changed.');
    if(tab==='game'&&doc.body.dataset.gamesApproved!=='true')throw new Error('Games access requires approval.');
    await preparePanelStyles(doc,new URL(targets[tab],location.origin).href,requestSignal);
    const root=doc.querySelector<HTMLElement>('[data-browse-page]');if(!root)throw new Error('Unable to load this tab.');
    const lifetime=new AbortController();
    signal.addEventListener('abort',()=>lifetime.abort(),{once:true});
    root.hidden=true;root.inert=true;host.append(root);
    try{prepare(root,tab,targets[tab],lifetime.signal);warmImages(root,lifetime.signal);return root;}
    catch(error){lifetime.abort();root.remove();throw error;}
  },signal);
  // The first panel already passed middleware and owns the current document.
  prepare(first,initial,location.pathname+location.search);tabs.set(initial,first);
  first.querySelector('[data-browse-tab-slot]')!.append(nav);updateNav(initial);

  function select(tab:BrowseTab){
    if(tab===wanted)return;
    if(shown===tabs.peek(active))positions.set(active,scrollY);wanted=tab;
    document.dispatchEvent(new Event('anylist:browse-tab-changed'));
    const activate=(root:HTMLElement)=>{
      if(signal.aborted||wanted!==tab)return;
      shown.removeAttribute('aria-busy');shown.querySelectorAll<HTMLElement>('[data-browse-form],[data-browse-region]').forEach(node=>node.inert=false);
      display(root,tab);active=tab;
      history.replaceState(history.state,'',root.dataset.browseUrl||targets[tab]);
      loading?.remove();loading=undefined;
      window.scrollTo(0,positions.get(tab)||0);
      root.dispatchEvent(new Event('anylist:browse-active'));
    };
    const ready=tabs.peek(tab);if(ready){activate(ready);return;}
    // Keep real content in place until the destination's own shell/skeleton is ready.
    shown.setAttribute('aria-busy','true');shown.querySelectorAll<HTMLElement>('[data-browse-form],[data-browse-region]').forEach(node=>node.inert=true);
    updateNav(tab);
    void tabs.get(tab).then(activate).catch(()=>{
      if(signal.aborted||wanted!==tab)return;
      shown.removeAttribute('aria-busy');shown.querySelectorAll<HTMLElement>('[data-browse-form],[data-browse-region]').forEach(node=>node.inert=false);
      const error=document.createElement('button');error.type='button';error.className='button-secondary';error.textContent='Try again';
      error.addEventListener('click',()=>{wanted=active;select(tab);},{signal});
      loading?.remove();loading=document.createElement('div');loading.className='browse-error';loading.append(error);shown.append(loading);
    });
  }
  nav.addEventListener('click',event=>{
    const link=(event.target as Element).closest<HTMLElement>('[data-browse-type]');
    if(!link||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    event.preventDefault();event.stopImmediatePropagation();select(link.dataset.browseType as BrowseTab);
  },{signal,capture:true});
  bindPredictionIntent(nav,signal,link=>{
    const tab=link.dataset.browseType as BrowseTab;
    if(tab&&tab!==wanted&&!tabs.peek(tab))void tabs.get(tab).catch(()=>{});
  });
}

document.addEventListener('astro:page-load',setup);
document.addEventListener('astro:before-swap',()=>{session?.abort();mounted=undefined;});
setup();
