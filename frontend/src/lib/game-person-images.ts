import {applyResponsiveArtwork} from './responsive-artwork.ts';

type Portrait={id:number;image:string|null;state:string};

/** Optional portraits follow visible people, never characters or hidden crews. */
export function mountGamePersonImages(root:HTMLElement,signal:AbortSignal) {
  const cache=new Map<number,Portrait>(),pending=new Set<number>(),observed=new WeakSet<Element>();
  const retries=new Map<number,number>(),retryTimers=new Set<ReturnType<typeof setTimeout>>();
  const loading=new WeakMap<HTMLElement,number>();
  const failed=new WeakMap<HTMLElement,string>();
  const eligible=(node:HTMLElement)=>node.matches('[data-game-person-portrait]')||
    ['actor','staff'].includes(node.dataset.personKind||'');
  const id=(node:HTMLElement)=>Number(node.dataset.gamePersonPortrait||node.dataset.personId);
  const usable=(node:HTMLElement)=>!!node.querySelector('img[src],img[data-deferred-src]');
  const nodes=()=>[...root.querySelectorAll<HTMLElement>('[data-person-id],[data-game-person-portrait]')].filter(eligible);
  let timer:ReturnType<typeof setTimeout>|undefined,running=false;
  const apply=(node:HTMLElement,value:Portrait)=>{
    if(!value.image||usable(node)||signal.aborted||!node.isConnected||id(node)!==value.id||loading.get(node)===value.id||failed.get(node)===value.image)return;
    const hero=node.hasAttribute('data-game-person-portrait');
    const holder=node.querySelector<HTMLElement>('.media-person-art')||node;
    const fallback=holder.querySelector<HTMLElement>('.lab-person-initials,[data-contributor-portrait-fallback]');
    if(!fallback)return;
    const image=new Image();image.alt=hero?node.dataset.portraitCaption||'':'';
    image.loading='eager';image.fetchPriority='low';
    loading.set(node,value.id);
    const cleanup=()=>{image.onload=image.onerror=null;loading.delete(node);signal.removeEventListener('abort',cleanup);};
    image.onload=()=>{
      cleanup();
      if(signal.aborted||!node.isConnected||id(node)!==value.id||usable(node))return;
      holder.prepend(image);fallback.hidden=true;
      if(hero){(node as HTMLButtonElement).disabled=false;node.dataset.portraitImage=value.image!;}
    };
    image.onerror=()=>{failed.set(node,value.image!);cleanup();};
    signal.addEventListener('abort',cleanup,{once:true});
    applyResponsiveArtwork(image,value.image,{size:hero?'w342':'w185',sizes:hero?'185px':'57px'});
  };
  const schedule=()=>{if(timer===undefined&&!running&&!signal.aborted)timer=setTimeout(()=>void flush(),40);};
  const flush=async()=>{
    timer=undefined;if(signal.aborted||running||!pending.size)return;
    running=true;const ids=[...pending].slice(0,16);ids.forEach(value=>pending.delete(value));
    try{
      const response=await fetch('/api/proxy/tracking/games/people/images',{method:'POST',signal,
        headers:{'Content-Type':'application/json'},body:JSON.stringify({ids}),cache:'no-store'});
      if(!response.ok)return;
      const data:{people:Portrait[]}=await response.json();
      if(signal.aborted)return;
      for(const value of data.people)if(ids.includes(value.id)){
        cache.set(value.id,value);
        const attempt=(retries.get(value.id)||0)+1;
        if(value.state==='pending'&&attempt<=6){
          retries.set(value.id,attempt);
          const timer=setTimeout(()=>{
            retryTimers.delete(timer);
            if(signal.aborted)return;
            cache.delete(value.id);pending.add(value.id);schedule();
          },attempt*3000);
          retryTimers.add(timer);
        }
      }
      for(const node of nodes()){const value=cache.get(id(node));if(value)apply(node,value);}
    }catch{/* Optional images never interrupt usable credits or page state. */}
    finally{running=false;if(pending.size)schedule();}
  };
  const observer=new IntersectionObserver(entries=>{
    for(const entry of entries){
      if(!entry.isIntersecting)continue;
      const node=entry.target as HTMLElement;observer.unobserve(node);
      if(!eligible(node)||usable(node))continue;
      const value=cache.get(id(node));
      if(value)apply(node,value);else if(Number.isSafeInteger(id(node))&&id(node)>0)pending.add(id(node));
    }
    if(pending.size)schedule();
  },{rootMargin:'160px'});
  const scan=()=>{
    for(const node of nodes()){
      const value=cache.get(id(node));
      if(value){apply(node,value);continue;}
      if(!observed.has(node)&&!usable(node)){observed.add(node);observer.observe(node);}
    }
  };
  const mutations=new MutationObserver(scan);
  mutations.observe(root,{childList:true,subtree:true});scan();
  signal.addEventListener('abort',()=>{observer.disconnect();mutations.disconnect();if(timer!==undefined)clearTimeout(timer);
    retryTimers.forEach(clearTimeout);pending.clear();cache.clear();},{once:true});
}
