import {applyResponsiveArtwork} from './responsive-artwork';

/** Shared compact-list artwork behavior, scoped to the current page lifetime. */
export function mountListCoverPreview(signal:AbortSignal,root:ParentNode=document){
  const preview=root.querySelector<HTMLElement>('#cover-preview'),image=preview?.querySelector<HTMLImageElement>('img');
  let dismissed=false,hovered:HTMLElement|null=null,shown:HTMLElement|null=null;
  let artwork:string|undefined;
  const editorOpen=()=>document.querySelector<HTMLDialogElement>('#tracking-editor')?.open;
  const hide=()=>{
    shown=null;
    if(preview)preview.hidden=true;
  };
  const show=(row:HTMLElement)=>{
    const thumbnail=row.querySelector<HTMLElement>('.entry-thumbnail'),source=thumbnail?.dataset.previewSrc;
    if(!preview||!source||dismissed||editorOpen()||!matchMedia('(hover:hover) and (min-width:901px)').matches){hide();return;}
    if(!image)return;
    if(artwork!==source){
      applyResponsiveArtwork(image,source,{size:'w500',sizes:'174px',route:'auto',compactIgdbCover:true});
      artwork=source;
    }
    const rect=thumbnail.getBoundingClientRect(),width=174,height=248,gap=18;
    preview.style.left=`${Math.max(8,Math.min(innerWidth-width-8,rect.left-width-gap>=8?rect.left-width-gap:rect.right+gap))}px`;
    preview.style.top=`${Math.max(8,Math.min(innerHeight-height-8,rect.top-(height-rect.height)/2))}px`;
    preview.hidden=false;shown=row;
  };
  const events=root as HTMLElement|Document;
  const rowFor=(event:Event)=>(event.target as Element).closest<HTMLElement>('.entry-row');
  events.addEventListener('pointerover',event=>{const row=rowFor(event);if(!row||row.contains((event as PointerEvent).relatedTarget as Node|null))return;hovered=row;dismissed=false;show(row);},{signal});
  events.addEventListener('pointerout',event=>{const row=rowFor(event);if(!row||row.contains((event as PointerEvent).relatedTarget as Node|null))return;if(hovered===row)hovered=null;if(shown===row)hide();if(!editorOpen()&&!('controlsDismissed' in row.dataset))delete row.dataset.editorDeselected;},{signal});
  events.addEventListener('focusin',event=>{const row=rowFor(event);if(!row||hovered||(event.target as Element).closest('[data-edit-entry],[data-edit-game]'))return;dismissed=false;show(row);},{signal});
  events.addEventListener('focusout',()=>{if(!hovered)hide();},{signal});
  events.addEventListener('click',event=>{
    const target=event.target as Element;
    if(target.closest('[data-season-toggle],[data-progress-increment]')){
      if(hovered)show(hovered);
    }else if(rowFor(event)!==hovered||!hovered||target.closest('a,button,input,select,textarea,[role="button"]'))hide();
  },{signal});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){dismissed=true;hide();}},{signal});
  window.addEventListener('scroll',()=>{if(hovered?.matches(':hover'))show(hovered);else hide();},{capture:true,signal});
  window.addEventListener('resize',hide,{signal});
  signal.addEventListener('abort',hide,{once:true});
  return {hide,refresh(){
    if(shown?.matches(':hover,:focus-within'))show(shown);
    else if(shown)hide();
  }};
}
