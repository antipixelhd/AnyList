import {responsiveArtwork} from './responsive-artwork.ts';
import {detailHeroArtwork} from './detail-artwork.ts';

type HeadingHint={year?:string;originalTitle?:string|null;tagline?:string|null;genres?:string[];creators?:string[];primaryPending?:boolean};
export type DetailHint={kind:'movie'|'series'|'game'|'unknown';title?:string;poster?:string|null;backdrop?:string|null;heading?:HeadingHint;library?:boolean};
export function detailDestination(url:URL){
  const match=url.pathname.match(/^\/(title|games)\/([1-9]\d*)\/?$/);
  if(match&&Number.isSafeInteger(Number(match[2])))return {key:`${match[1]}:${match[2]}`,kind:match[1]==='games'?'game':'unknown'} as const;
  const id=url.searchParams.get('id');if(!id||!/^\d+$/.test(id)||!Number.isSafeInteger(Number(id))||Number(id)<=0)return;
  if(url.pathname==='/discover-game')return {key:`igdb:${id}`,kind:'game'} as const;
  const kind=url.searchParams.get('type');
  if(url.pathname==='/discover-title'&&(kind==='movie'||kind==='series'))return {key:`tmdb:${kind}:${id}`,kind} as const;
}
/** Bounded viewer-scoped presentation hints, never an editable record. */
export class DetailHints {
  private viewer='';private hints=new Map<string,DetailHint>();
  scope(viewer:string){if(this.viewer!==viewer){this.viewer=viewer;this.hints.clear();}}
  get(url:URL){const key=detailDestination(url)?.key;return key?this.hints.get(key):undefined;}
  set(url:URL,hint:DetailHint){const key=detailDestination(url)?.key;if(!key)return;
    this.hints.delete(key);this.hints.set(key,{kind:hint.kind,title:hint.title?.slice(0,300),poster:hint.poster,backdrop:hint.backdrop,
      ...(hint.library!==undefined?{library:hint.library}:{}),...(hint.heading?{heading:{year:hint.heading.year,originalTitle:hint.heading.originalTitle,tagline:hint.heading.tagline,
        genres:hint.heading.genres?.slice(0,6),creators:hint.heading.creators?.slice(0,2),primaryPending:hint.heading.primaryPending}}:{})});
    while(this.hints.size>40)this.hints.delete(this.hints.keys().next().value!);
  }
}
export const detailHints=new DetailHints();
const safeImage=(path:string|null|undefined)=>path&&(path.startsWith('https://')||/^\/(?!\/)/.test(path))?path:undefined;
export function fillDetailLoading(root:HTMLElement,hint:DetailHint){
  root.dataset.loadingSince??=String(performance.now());
  root.dataset.loadingKind=hint.kind;
  root.dataset.loadingLibrary=String(!!hint.library);
  const title=root.querySelector<HTMLElement>('[data-loading-title]')!;
  if(hint.title)title.textContent=hint.title;
  root.querySelector<HTMLElement>('[data-loading-kind-label]')!.textContent=({movie:'Movie',series:'Series',game:'Game',unknown:''})[hint.kind];
  root.querySelector<HTMLElement>('[data-loading-series]')!.hidden=hint.kind!=='series';
  root.querySelector<HTMLElement>('[data-loading-secondary-label]')!.textContent=hint.kind==='game'?'Play time':'Cast & crew';
  const heading=hint.heading;
  for(const [key,value] of [['year',heading?.year],['creators',heading?.creators?.join(', ')],
    ['original-title',heading?.originalTitle?.trim().toLocaleLowerCase()!==hint.title?.trim().toLocaleLowerCase()?heading?.originalTitle:null],['tagline',heading?.tagline]]){
    const node=root.querySelector<HTMLElement>(`[data-loading-${key}]`);if(node){node.textContent=value||'';node.hidden=!value;}
  }
  const genres=root.querySelector<HTMLElement>('[data-loading-genres]');
  if(genres){genres.replaceChildren(...(heading?.genres||[]).slice(0,6).map(name=>{const span=root.ownerDocument.createElement('span');span.textContent=name;return span;}));genres.hidden=!genres.childElementCount&&!heading?.primaryPending;genres.setAttribute('aria-busy',String(!!heading?.primaryPending&&!genres.childElementCount));}
  const primary=root.querySelector<HTMLElement>('[data-loading-primary]');if(primary)primary.hidden=hint.kind!=='game'||!heading?.primaryPending;
  const artSpace=root.querySelector<HTMLElement>('.loading-detail-art-space');if(artSpace)artSpace.hidden=hint.kind==='game'||!hint.backdrop;
  const poster=root.querySelector<HTMLImageElement>('[data-loading-poster]')!,backdrop=root.querySelector<HTMLImageElement>('[data-loading-backdrop]')!;
  const posterURL=safeImage(hint.poster),backdropURL=safeImage(hint.backdrop);
  // Reuse an already selected responsive bucket from the source; do not warm a second size.
  for(const image of [poster,backdrop]){image.onload=()=>image.hidden=false;image.onerror=()=>image.hidden=true;image.fetchPriority='high';}
  if(posterURL){const artwork=responsiveArtwork(posterURL,{size:'w500',sizes:'(max-width:650px) 108px, (max-width:900px) 170px, (max-width:1100px) 190px, 210px'});if(artwork?.srcset)poster.srcset=artwork.srcset;if(artwork?.sizes)poster.sizes=artwork.sizes;poster.src=artwork?.src||posterURL;poster.hidden=!poster.complete||!poster.naturalWidth;}
  if(backdropURL){backdrop.src=detailHeroArtwork(backdropURL)?.src||backdropURL;backdrop.hidden=!backdrop.complete||!backdrop.naturalWidth;}
}
export function detailLoadingFrame(hint:DetailHint,doc:Document=document){
  const root=doc.querySelector<HTMLTemplateElement>('[data-detail-loading-template]')!.content.firstElementChild!.cloneNode(true) as HTMLElement;
  fillDetailLoading(root,hint);return root;
}
/** A content-only preview preserves the outgoing document's geometry/history. */
export function mountRoutePreview(host:HTMLElement,hint:DetailHint,url:URL,signal:AbortSignal){
  if(signal.aborted)return ()=>{};
  const root=detailLoadingFrame(hint);root.classList.add('route-detail-preview');
  const visibility=host.style.visibility,inert=host.inert;
  const overflow=document.documentElement.style.overflow;
  const header=document.querySelector<HTMLElement>('[data-appbar]'),headerHidden=header?.classList.contains('appbar-hidden');
  document.documentElement.style.overflow='hidden';header?.classList.remove('appbar-hidden');
  host.style.visibility='hidden';host.inert=true;host.before(root);
  const retry=root.querySelector<HTMLButtonElement>('[data-loading-retry]')!;
  const timer=setTimeout(()=>{root.dataset.loadingError='';root.querySelector('[data-loading-status]')!.textContent='This page is taking longer than expected.';retry.hidden=false;},15000);
  retry.addEventListener('click',()=>location.assign(url.href),{signal});
  const clear=()=>{clearTimeout(timer);root.remove();host.style.visibility=visibility;host.inert=inert;document.documentElement.style.overflow=overflow;if(headerHidden)header?.classList.add('appbar-hidden');};
  signal.addEventListener('abort',clear,{once:true});
  return ()=>{signal.removeEventListener('abort',clear);clear();};
}
