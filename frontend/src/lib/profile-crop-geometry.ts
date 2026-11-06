export type CropRect={x:number;y:number;width:number;height:number};
export const PROFILE_MOBILE_BACKGROUND_ASPECT=390/220;

/** Fit the entire source inside the editor's available space, in either orientation. */
export function profileImageFit(width:number,height:number,availableWidth:number,availableHeight:number) {
  const scale=Math.min(availableWidth/width,availableHeight/height);
  return {width:width*scale,height:height*scale};
}

/** Horizontal bounds of the narrower mobile crop, using the same saved focal framing. */
export function profileMobileRegion(rect:CropRect,width:number,height:number,aspect:number,mobileAspect:number,cropped=false) {
  // Uploads are physically cropped before storage; provider backgrounds retain
  // the full source and apply focal framing in the profile's CSS instead.
  if(cropped){
    const fraction=Math.min(1,mobileAspect/aspect);
    return fraction<1?{left:(1-fraction)*50,right:(1+fraction)*50}:null;
  }
  const framing=profileCrop(rect,width,height,aspect);
  const mobileWidth=Math.min(width,height*mobileAspect)/framing.zoom;
  if(mobileWidth>=rect.width)return null;
  const left=(rect.width-mobileWidth)*framing.x/100;
  return {left:left/rect.width*100,right:(left+mobileWidth)/rect.width*100};
}

/** Resize from the last pointer sample, not the beginning of an overshot gesture. */
export function resizeProfileSelection(rect:CropRect,dx:number,dy:number,action:string,aspect:number):CropRect {
  const direction=action.replace('-resize','');
  const horizontal=direction.includes('e')?1:direction.includes('w')?-1:0;
  const vertical=direction.includes('s')?1:direction.includes('n')?-1:0;
  const change=horizontal&&vertical?(dx*horizontal+dy*vertical/aspect)/(1+1/(aspect*aspect))
    :horizontal?dx*horizontal:dy*vertical*aspect;
  const width=Math.max(0,rect.width+change),height=width/aspect;
  return {x:rect.x-(width-rect.width)*(horizontal===-1?1:horizontal===0?.5:0),
    y:rect.y-(height-rect.height)*(vertical===-1?1:vertical===0?.5:0),width,height};
}

/** Clamp each drag axis independently and keep the opposite resize edge anchored. */
export function boundedProfileSelection(rect:CropRect,previous:CropRect,bounds:CropRect,aspect:number):CropRect {
  const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
  const same=(a:number,b:number)=>Math.abs(a-b)<.01;
  if(same(rect.width,previous.width)&&same(rect.height,previous.height))return {...rect,
    x:clamp(rect.x,bounds.x,bounds.x+bounds.width-rect.width),
    y:clamp(rect.y,bounds.y,bounds.y+bounds.height-rect.height)};
  const anchor=(position:number,size:number,oldPosition:number,oldSize:number)=>
    same(position,oldPosition)?0:same(position+size,oldPosition+oldSize)?1:.5;
  const ax=anchor(rect.x,rect.width,previous.x,previous.width),ay=anchor(rect.y,rect.height,previous.y,previous.height);
  const x=previous.x+previous.width*ax,y=previous.y+previous.height*ay;
  const room=(point:number,start:number,size:number,side:number)=>side===0?start+size-point:side===1?point-start:2*Math.min(point-start,start+size-point);
  const maximum=Math.max(0,Math.min(room(x,bounds.x,bounds.width,ax),room(y,bounds.y,bounds.height,ay)*aspect));
  const minimum=Math.min(maximum,Math.min(bounds.width,bounds.height*aspect)/3);
  const width=clamp(rect.width,minimum,maximum),height=width/aspect;
  return {x:x-width*ax,y:y-height*ay,width,height};
}

/** Translate a natural-image selection into the existing CSS focal framing. */
export function profileCrop(rect:CropRect,width:number,height:number,aspect:number) {
  const coverWidth=Math.min(width,height*aspect);
  const percent=(position:number,remaining:number)=>remaining>1e-6?Math.max(0,Math.min(100,position/remaining*100)):50;
  return {x:percent(rect.x,width-rect.width),y:percent(rect.y,height-rect.height),
    zoom:Math.max(1,Math.min(3,coverWidth/rect.width))};
}
