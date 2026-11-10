import {applyResponsiveArtwork,responsiveArtwork} from './responsive-artwork';
import {DecodedArtwork} from './decoded-artwork';

/** Shared compact-list artwork behavior, scoped to the current page lifetime. */
export function mountListCoverPreview(signal:AbortSignal,root:ParentNode=document,{cachePreviews=false}={}){
  const preview=root.querySelector<HTMLElement>('#cover-preview'),image=preview?.querySelector<HTMLImageElement>('img');
  const decoded=cachePreviews?new DecodedArtwork():undefined;
  let dismissed=false,hovered:HTMLElement|null=null,shown:HTMLElement|null=null;
  let timer:ReturnType<typeof setTimeout>|undefined,request=0;
  const editorOpen=()=>document.querySelector<HTMLDialogElement>('#tracking-editor')?.open;
  const hide=()=>{
    if(timer!==undefined)clearTimeout(timer);
    timer=undefined;request++;shown=null;
    if(preview)preview.hidden=true;
  };
  const show=(row:HTMLElement)=>{
    const thumbnail=row.querySelector<HTMLElement>('.entry-thumbnail'),source=thumbnail?.dataset.previewSrc;
    if(!preview||!image||!source||dismissed||editorOpen()||!matchMedia('(hover:hover) and (min-width:901px)').matches){hide();return;}
    const reveal=()=>{
      const rect=thumbnail!.getBoundingClientRect(),width=174,height=248,gap=18;
      preview.style.left=`${Math.max(8,Math.min(innerWidth-width-8,rect.left-width-gap>=8?rect.left-width-gap:rect.right+gap))}px`;
      preview.style.top=`${Math.max(8,Math.min(innerHeight-height-8,rect.top-(height-rect.height)/2))}px`;
      preview.hidden=false;shown=row;
    };
    if(!decoded){
      applyResponsiveArtwork(image,source,{size:'w500',sizes:'174px',route:'auto'});
      reveal();return;
    }
    hide();
    const version=request;
    const src=responsiveArtwork(source,{size:'w342'})?.src;
    if(!src)return;
    const paint=(ready:HTMLImageElement|undefined)=>{
      if(!ready||version!==request||editorOpen()||!row.isConnected||thumbnail?.dataset.previewSrc!==source)return;
      ready.alt='';
      if(ready.parentElement!==preview)preview.replaceChildren(ready);
      reveal();
    };
    const ready=decoded.get(src);
    if(ready){paint(ready);return;}
    // Virtualized rows can move underneath a stationary pointer during scroll.
    // Wait for intent before fetching a new preview and cancel on scroll or exit.
    timer=setTimeout(()=>{timer=undefined;void decoded.warm(src).then(()=>paint(decoded.get(src)));},150);
  };
  const events=root as HTMLElement|Document;
  const rowFor=(event:Event)=>(event.target as Element).closest<HTMLElement>('.entry-row');
  events.addEventListener('pointerover',event=>{const row=rowFor(event);if(!row||row.contains((event as PointerEvent).relatedTarget as Node|null))return;hovered=row;dismissed=false;show(row);},{signal});
  events.addEventListener('pointerout',event=>{const row=rowFor(event);if(!row||row.contains((event as PointerEvent).relatedTarget as Node|null))return;if(hovered===row){hovered=null;if(decoded)hide();}if(shown===row)hide();if(!editorOpen())delete row.dataset.editorDeselected;},{signal});
  events.addEventListener('focusin',event=>{const row=rowFor(event);if(!row||hovered||(event.target as Element).closest('[data-edit-entry],[data-edit-game]'))return;dismissed=false;show(row);},{signal});
  events.addEventListener('focusout',()=>{if(!hovered)hide();},{signal});
  events.addEventListener('click',hide,{signal});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){dismissed=true;hide();}},{signal});
  window.addEventListener('scroll',hide,{capture:true,signal});window.addEventListener('resize',hide,{signal});
  signal.addEventListener('abort',()=>{hide();decoded?.clear();},{once:true});
  return {hide};
}
