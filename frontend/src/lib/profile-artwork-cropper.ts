import Cropper from 'cropperjs';
import {profileCrop,type CropRect} from './profile-crop-geometry';

export async function mountProfileCropper(frame:HTMLElement,image:HTMLImageElement,aspect:number,
  signal:AbortSignal,onChange:(value:{x:number;y:number;zoom:number})=>void) {
  const handles=['n','e','s','w','ne','nw','se','sw'].map(direction=>`<cropper-handle action="${direction}-resize"></cropper-handle>`).join('');
  const cropper=new Cropper(image,{container:frame,template:`<cropper-canvas background>
    <cropper-image initial-center-size="contain"></cropper-image><cropper-shade hidden></cropper-shade>
    <cropper-selection movable resizable keyboard outlined precise>
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
    let bounds:CropRect;
    let crop:CropRect|undefined;
    const fit=()=>{
      // Keep the source immobile during editing; enable transforms only to fit.
      source.translatable=true;source.scalable=true;
      source.$center('contain');
      source.translatable=false;source.scalable=false;
      const outer=canvas.getBoundingClientRect(),inner=source.getBoundingClientRect();
      bounds={x:inner.x-outer.x,y:inner.y-outer.y,width:inner.width,height:inner.height};
      const width=Math.min(bounds.width,bounds.height*aspect),height=width/aspect;
      const scale=bounds.width/native.naturalWidth;
      selection.$change(crop?bounds.x+crop.x*scale:bounds.x+(bounds.width-width)/2,
        crop?bounds.y+crop.y*scale:bounds.y+(bounds.height-height)/2,
        crop?crop.width*scale:width,crop?crop.height*scale:height,aspect);
    };
    selection.addEventListener('change',event=>{
      if(!bounds)return;
      const rect=(event as CustomEvent<CropRect>).detail;
      const maxWidth=Math.min(bounds.width,bounds.height*aspect);
      if(rect.width<maxWidth/3-.1||rect.x<bounds.x-.1||rect.y<bounds.y-.1
        ||rect.x+rect.width>bounds.x+bounds.width+.1||rect.y+rect.height>bounds.y+bounds.height+.1){event.preventDefault();return;}
      const scale=native.naturalWidth/bounds.width;
      crop={x:(rect.x-bounds.x)*scale,y:(rect.y-bounds.y)*scale,width:rect.width*scale,height:rect.height*scale};
      onChange(profileCrop(crop,native.naturalWidth,native.naturalHeight,aspect));
    },{signal});
    fit();
    const observer=new ResizeObserver(()=>{if(!signal.aborted)fit();});observer.observe(frame);
    signal.addEventListener('abort',()=>observer.disconnect(),{once:true});
  }catch(error){destroy();throw error;}
}
