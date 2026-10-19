type Point={x:number;y:number};
export function zoomBounds(scale:number,x:number,y:number,width:number,height:number) {
  scale=Math.max(1,Math.min(4,scale));
  const clamp=(value:number,limit:number)=>Math.max(-limit,Math.min(limit,value));
  return {scale:Math.max(1,Math.min(4,scale)),x:clamp(x,width*(scale-1)/2),y:clamp(y,height*(scale-1)/2)};
}

/** Own phone image gestures before navigation/dismissal; never change page zoom. */
export function bindDetailImageZoom(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal,image:()=>HTMLImageElement|undefined,step:(direction:number)=>void,changed:()=>void) {
  const mobile=matchMedia('(max-width:650px), (max-height:650px)');
  const points=new Map<number,Point>();
  let scale=1,x=0,y=0,target:HTMLImageElement|undefined;
  let base={scale:1,x:0,y:0,distance:1,center:{x:0,y:0}};
  let origin:Point|undefined,moved=false,owned=false,ignoreUntil=0,lastTap=0,lastPoint:Point|undefined;
  const center=()=>{const rows=[...points.values()];return rows.length>1?{x:(rows[0].x+rows[1].x)/2,y:(rows[0].y+rows[1].y)/2}:rows[0];};
  const distance=()=>{const rows=[...points.values()];return rows.length>1?Math.hypot(rows[1].x-rows[0].x,rows[1].y-rows[0].y):1;};
  const paint=()=>{
    if(target)target.style.transform=`translate(${x}px,${y}px) scale(${scale})`;
    if(scale>1.01||points.size>1)dialog.dataset.viewerZoomed='';else delete dialog.dataset.viewerZoomed;
  };
  const rebase=()=>{base={scale,x,y,distance:distance(),center:center()||{x:0,y:0}};};
  const reset=()=>{
    if(target)target.style.removeProperty('transform');
    for(const id of points.keys())if(track.hasPointerCapture(id))track.releasePointerCapture(id);
    target=undefined;scale=1;x=0;y=0;points.clear();origin=undefined;owned=false;lastTap=0;
    delete dialog.dataset.viewerZoomed;changed();
  };
  const consume=(event:PointerEvent)=>{event.preventDefault();event.stopImmediatePropagation();};
  track.addEventListener('pointerdown',event=>{
    if(!mobile.matches||event.pointerType!=='touch'||!dialog.open)return;
    target=image();if(!target?.naturalWidth)return;
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(points.size===1){origin={x:event.clientX,y:event.clientY};moved=false;owned=scale>1.01;}
    if(points.size>1){owned=true;moved=true;dialog.dispatchEvent(new Event('viewerzoomstart'));}
    rebase();paint();
    track.setPointerCapture(event.pointerId);
    if(owned)consume(event);
  },{signal,capture:true});
  track.addEventListener('pointermove',event=>{
    if(!points.has(event.pointerId))return;
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(origin&&Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>8)moved=true;
    if(!owned)return;
    consume(event);
    const c=center(),rect=track.getBoundingClientRect();
    const next=Math.max(1,Math.min(4,base.scale*distance()/base.distance));
    const ratio=next/base.scale;
    // Keep the image point under the pinch midpoint stationary while scaling.
    const bounds=zoomBounds(next,c.x-rect.left-rect.width/2-(base.center.x-rect.left-rect.width/2-base.x)*ratio,
      c.y-rect.top-rect.height/2-(base.center.y-rect.top-rect.height/2-base.y)*ratio,rect.width,rect.height);
    scale=bounds.scale;x=bounds.x;y=bounds.y;paint();
  },{signal,capture:true,passive:false});
  const release=(event:PointerEvent)=>{
    if(!points.has(event.pointerId))return;
    const wasOwned=owned;
    points.delete(event.pointerId);
    if(track.hasPointerCapture(event.pointerId))track.releasePointerCapture(event.pointerId);
    if(wasOwned){consume(event);ignoreUntil=performance.now()+450;}
    if(points.size){rebase();return;}
    const now=performance.now();
    if(event.type!=='pointercancel'&&!wasOwned&&origin){
      const dx=event.clientX-origin.x,dy=event.clientY-origin.y;
      if(Math.abs(dx)>Math.max(40,track.clientWidth*.15)&&Math.abs(dx)>Math.abs(dy)*1.2){ignoreUntil=now+450;step(dx<0?1:-1);}
    }
    if(event.type!=='pointercancel'&&!moved&&origin){
      if(now-lastTap<300&&lastPoint&&Math.hypot(origin.x-lastPoint.x,origin.y-lastPoint.y)<32){
        if(scale>1.01){scale=1;x=0;y=0;}else{
          const rect=track.getBoundingClientRect();scale=2.5;
          ({x,y}=zoomBounds(scale,(rect.left+rect.width/2-origin.x)*(scale-1),(rect.top+rect.height/2-origin.y)*(scale-1),rect.width,rect.height));
        }
        ignoreUntil=now+450;lastTap=0;
      }else{lastTap=now;lastPoint=origin;}
    }else lastTap=0;
    if(scale<1.05){scale=1;x=0;y=0;}
    owned=false;origin=undefined;paint();changed();
  };
  window.addEventListener('pointerup',release,{signal,capture:true,passive:false});
  window.addEventListener('pointercancel',release,{signal,capture:true,passive:false});
  track.addEventListener('click',event=>{if(scale>1.01||performance.now()<ignoreUntil){event.preventDefault();event.stopImmediatePropagation();}},{signal,capture:true});
  dialog.addEventListener('click',event=>{if(scale>1.01){event.preventDefault();event.stopImmediatePropagation();}},{signal,capture:true});
  for(const event of ['blur','pagehide','pageshow','resize'])window.addEventListener(event,reset,{signal});
  dialog.addEventListener('close',reset,{signal});
  mobile.addEventListener('change',reset,{signal});
  signal.addEventListener('abort',reset,{once:true});
  return reset;
}
