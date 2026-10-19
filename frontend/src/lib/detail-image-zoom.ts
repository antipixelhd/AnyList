type Point={x:number;y:number};
export function zoomBounds(scale:number,x:number,y:number,width:number,height:number,viewportWidth=width,viewportHeight=height) {
  scale=Math.max(1,Math.min(4,scale));
  const clamp=(value:number,limit:number)=>Math.max(-limit,Math.min(limit,value));
  return {scale,x:clamp(x,Math.max(0,(width*scale-viewportWidth)/2)),y:clamp(y,Math.max(0,(height*scale-viewportHeight)/2))};
}

/** Own phone image gestures before navigation/dismissal; never change page zoom. */
export function bindDetailImageZoom(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal,image:()=>HTMLImageElement|undefined,step:(direction:number)=>void,changed:()=>void) {
  const mobile=matchMedia('(max-width:650px), (max-height:650px)');
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  let animation:Animation|undefined;
  const points=new Map<number,Point>();
  let scale=1,x=0,y=0,target:HTMLImageElement|undefined;
  let fitted:{width:number;height:number}|undefined;
  let frameTimer:ReturnType<typeof setTimeout>|undefined;
  const restoreFrame=()=>{
    clearTimeout(frameTimer);frameTimer=undefined;fitted=undefined;
    for(const property of ['--viewer-zoom-width','--viewer-zoom-height','--viewer-rest-height'])dialog.style.removeProperty(property);
    delete dialog.dataset.viewerZoomFrame;
  };
  let base={scale:1,x:0,y:0,distance:1,center:{x:0,y:0}};
  let origin:Point|undefined,moved=false,owned=false,ignoreUntil=0,lastTap=0,lastPoint:Point|undefined;
  const center=()=>{const rows=[...points.values()];return rows.length>1?{x:(rows[0].x+rows[1].x)/2,y:(rows[0].y+rows[1].y)/2}:rows[0];};
  const distance=()=>{const rows=[...points.values()];return rows.length>1?Math.hypot(rows[1].x-rows[0].x,rows[1].y-rows[0].y):1;};
  const paint=()=>{
    clearTimeout(frameTimer);frameTimer=undefined;
    if((scale>1.01||points.size>1)&&!fitted){
      const rect=track.getBoundingClientRect();fitted={width:rect.width,height:rect.height};
      dialog.style.setProperty('--viewer-zoom-width',`${rect.width}px`);
      dialog.style.setProperty('--viewer-zoom-height',`${rect.height}px`);
      dialog.style.setProperty('--viewer-rest-height',`${dialog.getBoundingClientRect().height}px`);
      dialog.dataset.viewerZoomFrame='';
    }
    if(target)target.style.transform=`translate(${x}px,${y}px) scale(${scale})`;
    if(scale>1.01||points.size>1)dialog.dataset.viewerZoomed='';else delete dialog.dataset.viewerZoomed;
    if(!('viewerZoomed' in dialog.dataset))delete dialog.dataset.viewerZoomControls;
    if(fitted&&!('viewerZoomed' in dialog.dataset)&&!points.size){
      if(reduced.matches)restoreFrame();else frameTimer=setTimeout(restoreFrame,240);
    }
  };
  const rebase=()=>{base={scale,x,y,distance:distance(),center:center()||{x:0,y:0}};};
  const reset=()=>{
    animation?.cancel();animation=undefined;
    if(target)target.style.removeProperty('transform');
    for(const id of points.keys())if(track.hasPointerCapture(id))track.releasePointerCapture(id);
    target=undefined;scale=1;x=0;y=0;points.clear();origin=undefined;owned=false;lastTap=0;
    restoreFrame();
    delete dialog.dataset.viewerZoomed;delete dialog.dataset.viewerZoomControls;changed();
  };
  const consume=(event:PointerEvent)=>{event.preventDefault();event.stopImmediatePropagation();};
  track.addEventListener('pointerdown',event=>{
    if(!mobile.matches||event.pointerType!=='touch'||!dialog.open)return;
    target=image();if(!target?.naturalWidth)return;
    if(animation){
      // Continue from the currently displayed frame if a finger interrupts zoom.
      const matrix=new DOMMatrixReadOnly(getComputedStyle(target).transform);
      scale=matrix.a;x=matrix.e;y=matrix.f;
      animation.cancel();animation=undefined;
    }
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(points.size===1){origin={x:event.clientX,y:event.clientY};moved=false;owned=scale>1.01;}
    if(points.size>1){owned=true;moved=true;delete dialog.dataset.viewerZoomControls;dialog.dispatchEvent(new Event('viewerzoomstart'));}
    rebase();paint();
    track.setPointerCapture(event.pointerId);
    if(owned)consume(event);
  },{signal,capture:true});
  track.addEventListener('pointermove',event=>{
    if(!points.has(event.pointerId))return;
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(origin&&Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>8)moved=true;
    if(!owned)return;
    if(moved)delete dialog.dataset.viewerZoomControls;
    consume(event);
    const c=center(),rect=track.getBoundingClientRect();
    const next=Math.max(1,Math.min(4,base.scale*distance()/base.distance));
    const ratio=next/base.scale;
    // Keep the image point under the pinch midpoint stationary while scaling.
    const bounds=zoomBounds(next,c.x-rect.left-rect.width/2-(base.center.x-rect.left-rect.width/2-base.x)*ratio,
      c.y-rect.top-rect.height/2-(base.center.y-rect.top-rect.height/2-base.y)*ratio,
      fitted?.width||rect.width,fitted?.height||rect.height,rect.width,rect.height);
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
    let animateFrom:string|undefined;
    if(event.type!=='pointercancel'&&!wasOwned&&origin){
      const dx=event.clientX-origin.x,dy=event.clientY-origin.y;
      if(Math.abs(dx)>Math.max(40,track.clientWidth*.15)&&Math.abs(dx)>Math.abs(dy)*1.2){ignoreUntil=now+450;step(dx<0?1:-1);}
    }
    if(event.type!=='pointercancel'&&!moved&&origin){
      if(now-lastTap<300&&lastPoint&&Math.hypot(origin.x-lastPoint.x,origin.y-lastPoint.y)<32){
        animateFrom=target?.style.transform;
        if(scale>1.01){scale=1;x=0;y=0;}else{
          const rect=track.getBoundingClientRect();scale=2.5;
          ({x,y}=zoomBounds(scale,(rect.left+rect.width/2-origin.x)*(scale-1),(rect.top+rect.height/2-origin.y)*(scale-1),
            fitted?.width||rect.width,fitted?.height||rect.height,rect.width,rect.height));
        }
        ignoreUntil=now+450;lastTap=0;
      }else{
        lastTap=now;lastPoint=origin;
        if(wasOwned){
          if('viewerZoomControls' in dialog.dataset)delete dialog.dataset.viewerZoomControls;
          else dialog.dataset.viewerZoomControls='';
        }
      }
    }else lastTap=0;
    if(scale<1.05){scale=1;x=0;y=0;}
    owned=false;origin=undefined;paint();changed();
    if(animateFrom&&target&&!reduced.matches){
      const running=target.animate([{transform:animateFrom},{transform:target.style.transform}],
        {duration:240,easing:'cubic-bezier(.2,0,0,1)'});
      animation=running;
      running.onfinish=()=>{if(animation===running)animation=undefined;};
    }
  };
  window.addEventListener('pointerup',release,{signal,capture:true,passive:false});
  window.addEventListener('pointercancel',release,{signal,capture:true,passive:false});
  track.addEventListener('click',event=>{if(scale>1.01||performance.now()<ignoreUntil){event.preventDefault();event.stopImmediatePropagation();}},{signal,capture:true});
  dialog.addEventListener('click',event=>{
    if(scale>1.01&&!('viewerZoomControls' in dialog.dataset&&(event.target as Element)?.closest?.('button'))){event.preventDefault();event.stopImmediatePropagation();}
  },{signal,capture:true});
  for(const event of ['blur','pagehide','pageshow','resize'])window.addEventListener(event,reset,{signal});
  dialog.addEventListener('close',reset,{signal});
  mobile.addEventListener('change',reset,{signal});
  signal.addEventListener('abort',reset,{once:true});
  return reset;
}
