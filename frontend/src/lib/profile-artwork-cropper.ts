import Cropper from 'cropperjs';
import {boundedProfileSelection,profileCrop,resizeProfileSelection,profileImageFit,type CropRect} from './profile-crop-geometry';

export async function mountProfileCropper(frame:HTMLElement,image:HTMLImageElement,aspect:number,
  signal:AbortSignal,onChange:(value:{x:number;y:number;zoom:number})=>void) {
  const handles=['n','e','s','w','ne','nw','se','sw'].map(direction=>`<cropper-handle action="${direction}-resize"></cropper-handle>`).join('');
  const cropper=new Cropper(image,{container:frame,template:`<cropper-canvas background>
    <cropper-image initial-center-size="contain"></cropper-image><cropper-shade hidden></cropper-shade>
    <cropper-selection movable resizable keyboard precise>
      <cropper-grid role="grid" covered></cropper-grid><cropper-crosshair centered></cropper-crosshair>
      <cropper-handle action="move" plain></cropper-handle>${handles}
    </cropper-selection></cropper-canvas>`});
  const source=cropper.getCropperImage()!,selection=cropper.getCropperSelection()!,canvas=cropper.getCropperCanvas()!;
  selection.aspectRatio=aspect;
  const destroy=()=>cropper.destroy();
  signal.addEventListener('abort',destroy,{once:true});
  try{
    const native=await source.$ready();
    if(signal.aborted)return;
    const stage=frame.parentElement!;
    let bounds:CropRect;
    let crop:CropRect|undefined;
    let fitting=false;
    // Cropper's built-in resize retains the pointer-down origin after clamping.
    // Consume its pointer samples incrementally so reversal responds immediately.
    canvas.addEventListener('action',event=>{
      const detail=(event as CustomEvent<{action:string;startX:number;startY:number;endX:number;endY:number}>).detail;
      if(!bounds||!detail.action?.endsWith('-resize'))return;
      event.preventDefault();
      const proposed=resizeProfileSelection(selection,detail.endX-detail.startX,detail.endY-detail.startY,detail.action,aspect);
      const rect=boundedProfileSelection(proposed,selection,bounds,aspect);
      selection.$change(rect.x,rect.y,rect.width,rect.height,aspect);
    },{signal,capture:true});
    const fit=()=>{
      const size=profileImageFit(native.naturalWidth,native.naturalHeight,stage.clientWidth,stage.clientHeight);
      if(size.width<=0||size.height<=0)return;
      frame.style.width=`${size.width}px`;frame.style.height=`${size.height}px`;
      // Keep the source immobile during editing; enable transforms only to fit.
      source.translatable=true;source.scalable=true;
      source.$resetTransform();
      source.$center('contain');
      source.translatable=false;source.scalable=false;
      const outer=canvas.getBoundingClientRect(),inner=source.getBoundingClientRect();
      bounds={x:inner.x-outer.x,y:inner.y-outer.y,width:inner.width,height:inner.height};
      const width=Math.min(bounds.width,bounds.height*aspect),height=width/aspect;
      const scale=bounds.width/native.naturalWidth;
      fitting=true;
      selection.$change(crop?bounds.x+crop.x*scale:bounds.x+(bounds.width-width)/2,
        crop?bounds.y+crop.y*scale:bounds.y+(bounds.height-height)/2,
        crop?crop.width*scale:width,crop?crop.height*scale:height,aspect);
      fitting=false;
    };
    selection.addEventListener('change',event=>{
      if(!bounds)return;
      const rect=(event as CustomEvent<CropRect>).detail;
      const bounded=!fitting&&selection.width>0?boundedProfileSelection(rect,selection,bounds,aspect):rect;
      if((['x','y','width','height'] as const).some(key=>Math.abs(bounded[key]-rect[key])>1e-6)){
        event.preventDefault();selection.$change(bounded.x,bounded.y,bounded.width,bounded.height,aspect);return;
      }
      const scale=native.naturalWidth/bounds.width;
      crop={x:(rect.x-bounds.x)*scale,y:(rect.y-bounds.y)*scale,width:rect.width*scale,height:rect.height*scale};
      onChange(profileCrop(crop,native.naturalWidth,native.naturalHeight,aspect));
    },{signal});
    fit();
    const observer=new ResizeObserver(()=>{if(!signal.aborted)fit();});observer.observe(stage);
    signal.addEventListener('abort',()=>observer.disconnect(),{once:true});
  }catch(error){destroy();throw error;}
}
