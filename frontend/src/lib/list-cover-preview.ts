import {applyResponsiveArtwork} from './responsive-artwork';

/** Shared compact-list artwork behavior, scoped to the current page lifetime. */
export function mountListCoverPreview(signal:AbortSignal,root:ParentNode=document){
  const preview=root.querySelector<HTMLElement>('#cover-preview'),image=preview?.querySelector<HTMLImageElement>('img');
  let dismissed=false,hovered:HTMLElement|null=null,shown:HTMLElement|null=null;
  const hide=()=>{shown=null;if(preview)preview.hidden=true;};
  const show=(row:HTMLElement)=>{
    const thumbnail=row.querySelector<HTMLElement>('.entry-thumbnail'),source=thumbnail?.dataset.previewSrc;
    if(!preview||!image||!source||dismissed||document.querySelector<HTMLDialogElement>('#tracking-editor')?.open||!matchMedia('(hover:hover) and (min-width:901px)').matches){hide();return;}
    const rect=thumbnail!.getBoundingClientRect(),width=174,height=248,gap=18;
    applyResponsiveArtwork(image,source,{size:'w500',sizes:'174px',route:'auto'});
    preview.style.left=`${Math.max(8,Math.min(innerWidth-width-8,rect.left-width-gap>=8?rect.left-width-gap:rect.right+gap))}px`;
    preview.style.top=`${Math.max(8,Math.min(innerHeight-height-8,rect.top-(height-rect.height)/2))}px`;
    preview.hidden=false;shown=row;
  };
  root.querySelectorAll<HTMLElement>('.entry-row').forEach(row=>{
    row.addEventListener('pointerenter',()=>{hovered=row;dismissed=false;show(row);},{signal});
    row.addEventListener('pointerleave',()=>{if(hovered===row)hovered=null;if(shown===row)hide();if(!document.querySelector<HTMLDialogElement>('#tracking-editor')?.open)delete row.dataset.editorDeselected;},{signal});
    row.addEventListener('focusin',event=>{if(hovered||(event.target as Element).closest('[data-edit-entry],[data-edit-game]'))return;dismissed=false;show(row);},{signal});
    row.addEventListener('focusout',()=>{if(!hovered&&shown===row)hide();},{signal});
    row.addEventListener('click',hide,{signal});
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){dismissed=true;hide();}},{signal});
  window.addEventListener('scroll',hide,{capture:true,signal});window.addEventListener('resize',hide,{signal});
  signal.addEventListener('abort',hide,{once:true});
  return {hide};
}
