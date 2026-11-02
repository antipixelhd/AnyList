export type CropRect={x:number;y:number;width:number;height:number};

/** Translate a natural-image selection into the existing CSS focal framing. */
export function profileCrop(rect:CropRect,width:number,height:number,aspect:number) {
  const coverWidth=Math.min(width,height*aspect);
  const percent=(position:number,remaining:number)=>remaining>1e-6?Math.max(0,Math.min(100,position/remaining*100)):50;
  return {x:percent(rect.x,width-rect.width),y:percent(rect.y,height-rect.height),
    zoom:Math.max(1,Math.min(3,coverWidth/rect.width))};
}
