import {preparePanelStyles} from './panel-readiness';
import {initializeBrowse} from './browse';
import {initializeGameBrowse} from './game-browse';
import {initializeScrollMotion} from './scroll-motion';
import {bindPredictionIntent,predictionAllowed,predictionManifest} from './predictive-loading';
import {browseTab,browseTabHref,createBrowseTabContext,createBrowseTabs,scopeBrowseIds,type ApplyBrowseTabContext,type BrowseTab} from './browse-tabs';

let session:AbortController|undefined;
let mounted:HTMLElement|undefined;
const documentNavigation = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
let restoring = documentNavigation?.name === location.href &&
  (documentNavigation.type === 'reload' || documentNavigation.type === 'back_forward');

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
  const context=createBrowseTabContext(region||'');
  const applyContext=new WeakMap<HTMLElement,ApplyBrowseTabContext>();
  const posterMotion = new WeakMap<HTMLElement, NonNullable<ReturnType<typeof initializeScrollMotion>>>();
  const positions=new Map<BrowseTab,number>();
  let active=initial,wanted=initial,shown=first,loading:HTMLElement|undefined;

  function capture(){
    const form=shown.querySelector<HTMLFormElement>('[data-browse-form]')!;
    const values=new URLSearchParams();
    // Disabled sort controls are outside the shared context. Read the other
    // live values even during a debounced search or a pending tab switch.
    for(const control of form.elements)if(control instanceof HTMLInputElement||control instanceof HTMLSelectElement){
      if(control.name)values.set(control.name,control.value);
    }
    context.capture(active,values,form.querySelector<HTMLDetailsElement>('.browse-more')?.open);
  }
  const target=(tab:BrowseTab)=>browseTabHref(tab,context.forTab(tab).values,tabs.peek(tab)?.dataset.browseUrl);

  function prepare(root:HTMLElement,tab:BrowseTab,url:string,panelSignal=signal){
    root.dataset.browseTab=tab;root.dataset.browseUrl=url;
    root.addEventListener('anylist:browse-url',()=>{if(root===shown)capture();updateNav(wanted);},{signal:panelSignal});
    scopeBrowseIds(root,tab);
    const oldNav=root.querySelector('.browse-types')!;
    const slot=document.createElement('span');slot.dataset.browseTabSlot='';oldNav.replaceWith(slot);
    if (root===first) slot.append(nav);
    applyContext.set(root,tab==='game'?initializeGameBrowse(root,panelSignal):initializeBrowse(root,panelSignal));
    const motion = initializeScrollMotion(root, panelSignal, '.browse-card-body, .browse-skeleton-body', 'browse-scroll-reveal', {
      animateInitial: !restoring, deferHidden: true, downwardOnly: true, scaleEntrance: true,
      once: true, appearedAttribute: 'data-browse-appeared',
    });
    if (motion) posterMotion.set(root, motion);
    return root;
  }
  function updateNav(tab:BrowseTab){
    nav.querySelectorAll<HTMLAnchorElement>('[data-browse-type]').forEach(link=>{
      const type=link.dataset.browseType as BrowseTab;
      link.href=target(type);
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
    const url=target(tab);
    const response=await fetch(url,{signal:requestSignal,credentials:'same-origin',headers:{Accept:'text/html'}});
    if(!response.ok||response.redirected)throw new Error('Unable to load this tab.');
    const html=await response.text();
    if(requestSignal.aborted||!predictionManifest(html,viewer||'anonymous',tab==='game'))throw new Error('Account changed.');
    const doc=new DOMParser().parseFromString(html,'text/html');
    if(requestSignal.aborted||doc.body.dataset.cacheUser!==viewer)throw new Error('Account changed.');
    if(tab==='game'&&doc.body.dataset.gamesApproved!=='true')throw new Error('Games access requires approval.');
    await preparePanelStyles(doc,new URL(url,location.origin).href,requestSignal);
    const root=doc.querySelector<HTMLElement>('[data-browse-page]');if(!root)throw new Error('Unable to load this tab.');
    root.querySelectorAll('noscript').forEach(node=>node.remove());
    const lifetime=new AbortController();
    signal.addEventListener('abort',()=>lifetime.abort(),{once:true});
    root.hidden=true;root.inert=true;host.append(root);
    try{prepare(root,tab,url,lifetime.signal);warmImages(root,lifetime.signal);return root;}
    catch(error){lifetime.abort();root.remove();throw error;}
  },signal);
  // The first panel already passed middleware and owns the current document.
  prepare(first,initial,location.pathname+location.search);tabs.set(initial,first);
  capture();updateNav(initial);

  function select(tab:BrowseTab){
    if(tab===wanted)return;
    capture();
    if(shown===tabs.peek(active))positions.set(active,scrollY);wanted=tab;
    document.dispatchEvent(new Event('anylist:browse-tab-changed'));
    const activate=(root:HTMLElement)=>{
      if(signal.aborted||wanted!==tab)return;
      shown.removeAttribute('data-browse-waiting');shown.removeAttribute('aria-busy');shown.querySelectorAll<HTMLElement>('[data-browse-form],[data-browse-region]').forEach(node=>node.inert=false);
      const shared=context.forTab(tab);
      applyContext.get(root)?.(shared.values,shared.moreOpen);
      active=tab;display(root,tab);
      history.replaceState(history.state,'',root.dataset.browseUrl||target(tab));
      loading?.remove();loading=undefined;
      window.scrollTo(0,positions.get(tab)||0);
      posterMotion.get(root)?.replayVisible();
      root.dispatchEvent(new Event('anylist:browse-active'));
    };
    const ready=tabs.peek(tab);if(ready){activate(ready);return;}
    // Keep real content in place until the destination's own shell/skeleton is ready.
    shown.setAttribute('data-browse-waiting','');shown.setAttribute('aria-busy','true');shown.querySelectorAll<HTMLElement>('[data-browse-form],[data-browse-region]').forEach(node=>node.inert=true);
    updateNav(tab);
    void tabs.get(tab).then(activate).catch(()=>{
      if(signal.aborted||wanted!==tab)return;
      shown.removeAttribute('data-browse-waiting');shown.removeAttribute('aria-busy');shown.querySelectorAll<HTMLElement>('[data-browse-form],[data-browse-region]').forEach(node=>node.inert=false);
      wanted=active;updateNav(active);
      const error=document.createElement('button');error.type='button';error.className='button-secondary';error.textContent='Try again';
      error.addEventListener('click',()=>select(tab),{signal});
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
    capture();
    if(tab&&tab!==wanted&&!tabs.peek(tab))void tabs.get(tab).catch(()=>{});
  });
}

document.addEventListener('astro:page-load',setup);
document.addEventListener('astro:before-swap',event=>{
  restoring=event.navigationType==='traverse';session?.abort();mounted=undefined;
});
setup();
