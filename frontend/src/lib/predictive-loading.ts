export function predictionAllowed(network: {onLine?:boolean;connection?:{saveData?:boolean;effectiveType?:string}} = navigator) {
  return network.onLine!==false&&!network.connection?.saveData&&!['slow-2g','2g','3g'].includes(network.connection?.effectiveType||'');
}

/** Read only JSON from HTML text. Parsing a detached document can fetch images. */
export type PredictionArtwork={src:string;srcset?:string;sizes?:string};
export function predictionManifest(html:string,user:string) {
  const script=html.match(/<script\b[^>]*\bid=["']page-prediction["'][^>]*>([\s\S]*?)<\/script>/i);
  if(!script)return;
  try{
    const value=JSON.parse(script[1]);
    if(value.user!==user||!Array.isArray(value.artwork))return;
    return value.artwork.map((image:unknown)=>typeof image==='string'?{src:image}:image)
      .filter((image:any)=>typeof image?.src==='string'&&(image.src.startsWith('https://')||image.src.startsWith('/api/proxy/media/image/')))
      .slice(0,2) as PredictionArtwork[];
  }catch{return;}
}

export function predictivePage(url:URL,origin:string) {
  return url.origin===origin&&!url.hash&&(/^\/title\/[1-9]\d*$/.test(url.pathname)||
    /^\/(actors|person|studio)\/(catalogue|tmdb)(:|%3A)[1-9]\d*$/i.test(url.pathname));
}

/** One speculative result, shared with an actual request, and never persisted. */
export function createPrediction<T>(fetcher:(key:string,signal:AbortSignal)=>Promise<T>,signal:AbortSignal,
  allowed=predictionAllowed,now=()=>Date.now(),ttl=30_000) {
  let slot:{key:string;controller:AbortController;pending:Promise<T|undefined>;expires:number}|undefined;
  const cancel=()=>{slot?.controller.abort();slot=undefined;};
  const current=(key:string)=>{
    if(slot&&slot.expires<=now())cancel();
    return slot?.key===key?slot:undefined;
  };
  signal.addEventListener('abort',cancel,{once:true});
  return {
    cancel,
    prefetch(key:string){
      if(signal.aborted||!allowed()||current(key))return;
      cancel();
      const controller=new AbortController();
      const entry:NonNullable<typeof slot>={key,controller,expires:now()+ttl,pending:Promise.resolve<T|undefined>(undefined)};
      slot=entry;
      entry.pending=fetcher(key,controller.signal).then(value=>controller.signal.aborted?undefined:value).catch(()=>undefined);
    },
    async consume(key:string,requestSignal:AbortSignal):Promise<T|undefined>{
      const entry=current(key);
      if(!entry||requestSignal.aborted)return;
      return new Promise((resolve,reject)=>{
        const abort=()=>reject(new DOMException('Aborted','AbortError'));
        requestSignal.addEventListener('abort',abort,{once:true});
        void entry.pending.then(value=>{
          requestSignal.removeEventListener('abort',abort);
          if(slot===entry)slot=undefined;
          if(!requestSignal.aborted)resolve(value);
        });
      });
    },
    has(key:string){return !!current(key);},
  };
}

/** A brief dwell avoids loading links merely passed by the pointer. */
export function bindPredictionIntent(root:Document|HTMLElement,signal:AbortSignal,predict:(link:HTMLAnchorElement)=>void) {
  let timer:ReturnType<typeof setTimeout>|undefined,target:HTMLAnchorElement|undefined;
  const clear=()=>{clearTimeout(timer);target=undefined;};
  const linkFor=(event:Event)=>(event.target as Element)?.closest<HTMLAnchorElement>('a[href]')||undefined;
  const schedule=(event:Event)=>{
    if(event instanceof PointerEvent&&event.pointerType!=='mouse')return;
    const link=linkFor(event);
    if(!link||target===link)return;
    clear();target=link;
    timer=setTimeout(()=>{if(!signal.aborted&&link.isConnected&&target===link&&predictionAllowed())predict(link);},180);
  };
  root.addEventListener('pointerover',schedule,{signal,passive:true});
  root.addEventListener('focusin',schedule,{signal});
  root.addEventListener('pointerout',event=>{
    if(target&&!target.contains((event as PointerEvent).relatedTarget as Node|null))clear();
  },{signal});
  root.addEventListener('focusout',clear,{signal});
  root.addEventListener('pointerdown',event=>{
    if((event as PointerEvent).pointerType!=='touch'||!predictionAllowed())return;
    const link=linkFor(event);if(link)predict(link);
  },{signal,passive:true});
  signal.addEventListener('abort',clear,{once:true});
}

export function paginationPosition(rect:{top:number;height:number;bottom:number},viewport:number) {
  const travel=Math.max(1,rect.height-viewport);
  return {predict:Math.max(0,-rect.top)/travel>=.7,append:rect.bottom-viewport<=500};
}

export function bindPredictivePagination(region:HTMLElement,signal:AbortSignal,enabled:()=>boolean,
  predict:()=>void,append:()=>void) {
  let previous=window.scrollY,frame=0;
  window.addEventListener('scroll',()=>{
    const down=window.scrollY>previous;previous=window.scrollY;
    if(!down||frame||!enabled())return;
    frame=requestAnimationFrame(()=>{
      frame=0;if(signal.aborted||!enabled())return;
      const position=paginationPosition(region.getBoundingClientRect(),window.innerHeight);
      if(position.predict)predict();
      if(position.append)append();
    });
  },{signal,passive:true});
  signal.addEventListener('abort',()=>cancelAnimationFrame(frame),{once:true});
}
