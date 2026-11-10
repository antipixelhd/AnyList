import {applyResponsiveArtwork} from './responsive-artwork';

/** Shared compact-list artwork behavior, scoped to the current page lifetime. */
export function mountListCoverPreview(signal:AbortSignal,root:ParentNode=document,{reuseThumbnail=false}={}){
  const preview=root.querySelector<HTMLElement>('#cover-preview'),image=preview?.querySelector<HTMLImageElement>('img');
  const thumbnailPreview=reuseThumbnail?document.createElement('canvas'):undefined;
  let dismissed=false,hovered:HTMLElement|null=null,shown:HTMLElement|null=null;
  let waiting:HTMLImageElement|undefined;
  const editorOpen=()=>document.querySelector<HTMLDialogElement>('#tracking-editor')?.open;
  const hide=()=>{
    waiting=undefined;shown=null;
    if(preview)preview.hidden=true;
  };
  const show=(row:HTMLElement)=>{
    const thumbnail=row.querySelector<HTMLElement>('.entry-thumbnail'),source=thumbnail?.dataset.previewSrc;
    if(!preview||!source||dismissed||editorOpen()||!matchMedia('(hover:hover) and (min-width:901px)').matches){hide();return;}
    if(thumbnailPreview){
      const cover=thumbnail.querySelector<HTMLImageElement>('img.entry-cover');
      if(!cover){hide();return;}
      if(!cover.complete){hide();waiting=cover;return;}
      const context=thumbnailPreview.getContext('2d');
      if(!context||!cover.naturalWidth||!cover.naturalHeight){hide();return;}
      // Reuse the row's pixels without a second image source or download.
      const scale=Math.min(1,480/Math.max(cover.naturalWidth,cover.naturalHeight));
      thumbnailPreview.width=Math.max(1,Math.round(cover.naturalWidth*scale));
      thumbnailPreview.height=Math.max(1,Math.round(cover.naturalHeight*scale));
      context.drawImage(cover,0,0,thumbnailPreview.width,thumbnailPreview.height);
      if(thumbnailPreview.parentElement!==preview)preview.replaceChildren(thumbnailPreview);
    }else{
      if(!image)return;
      applyResponsiveArtwork(image,source,{size:'w500',sizes:'174px',route:'auto'});
    }
    const rect=thumbnail.getBoundingClientRect(),width=174,height=248,gap=18;
    preview.style.left=`${Math.max(8,Math.min(innerWidth-width-8,rect.left-width-gap>=8?rect.left-width-gap:rect.right+gap))}px`;
    preview.style.top=`${Math.max(8,Math.min(innerHeight-height-8,rect.top-(height-rect.height)/2))}px`;
    waiting=undefined;preview.hidden=false;shown=row;
  };
  const events=root as HTMLElement|Document;
  const rowFor=(event:Event)=>(event.target as Element).closest<HTMLElement>('.entry-row');
  if(reuseThumbnail)events.addEventListener('load',event=>{
    if(event.target!==waiting)return;
    const row=rowFor(event);if(row)show(row);
  },{capture:true,signal});
  events.addEventListener('pointerover',event=>{const row=rowFor(event);if(!row||row.contains((event as PointerEvent).relatedTarget as Node|null))return;hovered=row;dismissed=false;show(row);},{signal});
  events.addEventListener('pointerout',event=>{const row=rowFor(event);if(!row||row.contains((event as PointerEvent).relatedTarget as Node|null))return;if(hovered===row)hovered=null;if(shown===row||waiting&&row.contains(waiting))hide();if(!editorOpen())delete row.dataset.editorDeselected;},{signal});
  events.addEventListener('focusin',event=>{const row=rowFor(event);if(!row||hovered||(event.target as Element).closest('[data-edit-entry],[data-edit-game]'))return;dismissed=false;show(row);},{signal});
  events.addEventListener('focusout',()=>{if(!hovered)hide();},{signal});
  events.addEventListener('click',hide,{signal});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){dismissed=true;hide();}},{signal});
  window.addEventListener('scroll',hide,{capture:true,signal});window.addEventListener('resize',hide,{signal});
  signal.addEventListener('abort',hide,{once:true});
  return {hide};
}
