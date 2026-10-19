import {createDetailImageSwipe} from './detail-image-swipe';

type Point={x:number;y:number};
export function zoomBounds(scale:number,x:number,y:number,width:number,height:number,viewportWidth=width,viewportHeight=height,keepAnchor=false) {
  scale=Math.max(1,Math.min(4,scale));
  const clamp=(value:number,limit:number)=>limit>0?Math.max(-limit,Math.min(limit,value)):0;
  const limit=(size:number,viewport:number)=>keepAnchor?Math.abs(size*scale-viewport)/2:Math.max(0,(size*scale-viewport)/2);
  return {scale,x:clamp(x,limit(width,viewportWidth)),y:clamp(y,limit(height,viewportHeight))};
}

/** A contact sequence owns exactly one gesture: swipe/dismiss at fit, or pinch/pan. */
export function bindDetailImageZoom(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal,image:()=>HTMLImageElement|undefined,select:(index:number)=>void,changed:()=>void,beforeGesture:()=>void=()=>{}) {
  const mobile=matchMedia('(max-width:650px), (max-height:650px)');
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  const swipe=createDetailImageSwipe(track,signal,select);
  const points=new Map<number,Point>();
  let target:HTMLImageElement|undefined,scale=1,x=0,y=0;
  let fitted:{width:number;height:number;box:DOMRect;center:Point}|undefined;
  let animation:Animation|undefined,frameAnimation:Animation|undefined,returning=false;
  let selectionFrame=0,selectionIndex:number|undefined,incoming:HTMLImageElement|undefined;
  const cancelSelection=()=>{
    cancelAnimationFrame(selectionFrame);selectionFrame=0;selectionIndex=undefined;
    delete track.dataset.viewerSwiping;
    if(incoming!==target)incoming?.style.removeProperty('transform');incoming=undefined;
  };
  let origin:Point|undefined,axis:'horizontal'|'vertical'|undefined,moved=false,owned=false;
  let ignoreUntil=0,lastTap=0,lastPoint:Point|undefined,samples:{x:number;time:number}[]=[];
  let base={scale:1,distance:1,anchor:{x:0,y:0}};
  const center=()=>{const rows=[...points.values()];return rows.length>1?{x:(rows[0].x+rows[1].x)/2,y:(rows[0].y+rows[1].y)/2}:rows[0];};
  const distance=()=>{const rows=[...points.values()];return rows.length>1?Math.max(1,Math.hypot(rows[1].x-rows[0].x,rows[1].y-rows[0].y)):1;};
  const stageCenter=()=>{const r=track.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};};
  const transform=()=>`translate(${x}px,${y}px) scale(${scale})`;
  const paint=()=>{if(target)target.style.transform=transform();};
  const restoreFrame=()=>{
    const index=Math.round(track.scrollLeft/Math.max(1,track.clientWidth));
    frameAnimation?.cancel();frameAnimation=undefined;fitted=undefined;returning=false;
    for(const property of ['--viewer-zoom-width','--viewer-zoom-height'])dialog.style.removeProperty(property);
    delete dialog.dataset.viewerZoomFrame;delete dialog.dataset.viewerZoomed;delete dialog.dataset.viewerZoomControls;
    track.scrollLeft=index*track.clientWidth;
  };
  const clip=()=>{
    const r=dialog.getBoundingClientRect(),box=fitted!.box;
    return `inset(${Math.max(0,box.top-r.top)}px ${Math.max(0,r.right-box.right)}px ${Math.max(0,r.bottom-box.bottom)}px ${Math.max(0,box.left-r.left)}px round 8px)`;
  };
  const expand=()=>{
    if(fitted)return;
    const index=Math.round(track.scrollLeft/Math.max(1,track.clientWidth));
    const rect=track.getBoundingClientRect();
    fitted={width:rect.width,height:rect.height,box:dialog.getBoundingClientRect(),center:stageCenter()};
    dialog.style.setProperty('--viewer-zoom-width',`${rect.width}px`);
    dialog.style.setProperty('--viewer-zoom-height',`${rect.height}px`);
    dialog.dataset.viewerZoomFrame='';dialog.dataset.viewerZoomed='';
    track.scrollLeft=index*track.clientWidth;
    // Change layout once, then animate only the clipping aperture. Resizing the
    // carousel on every animation frame fights scroll snapping and pinch math.
    if(!reduced.matches)frameAnimation=dialog.animate([{clipPath:clip()},{clipPath:'inset(0px round 0px)'}],{duration:200,easing:'cubic-bezier(.2,0,0,1)'});
  };
  const rebase=()=>{
    const c=center()||{x:0,y:0},stage=stageCenter();
    base={scale,distance:distance(),anchor:{x:(c.x-stage.x-x)/scale,y:(c.y-stage.y-y)/scale}};
  };
  const interruptAnimation=()=>{
    cancelSelection();
    if(!animation||!target)return;
    const matrix=new DOMMatrixReadOnly(getComputedStyle(target).transform);
    scale=matrix.a;x=matrix.e;y=matrix.f;animation.cancel();animation=undefined;
    if(returning){
      const from=getComputedStyle(dialog).clipPath;
      frameAnimation?.cancel();returning=false;
      if(!reduced.matches)frameAnimation=dialog.animate([{clipPath:from},{clipPath:'inset(0px round 0px)'}],{duration:160,easing:'ease-out'});
    }
  };
  const animateImage=(from:string,done?:()=>void)=>{
    paint();
    if(!target||reduced.matches){done?.();return;}
    const running=target.animate([{transform:from},{transform:transform()}],{duration:200,easing:'cubic-bezier(.2,0,0,1)'});
    animation=running;running.onfinish=()=>{if(animation===running){animation=undefined;done?.();}};
  };
  const returnToFit=(done?:()=>void,index?:number)=>{
    if(!fitted){scale=1;x=0;y=0;paint();changed();done?.();return;}
    const from=transform(),stage=stageCenter();
    scale=1;x=fitted.center.x-stage.x;y=fitted.center.y-stage.y;returning=true;
    if(index!==undefined){
      selectionIndex=index;
      track.dataset.viewerSwiping='';
      incoming=track.children[index]?.querySelector<HTMLImageElement>('img')||undefined;
      if(incoming&&incoming!==target)incoming.style.transform=`translate(${x}px,${y}px)`;
      const fromLeft=track.scrollLeft,toLeft=index*track.clientWidth,began=performance.now();
      const tick=(now:number)=>{
        const progress=Math.min(1,(now-began)/200);
        track.scrollLeft=fromLeft+(toLeft-fromLeft)*(1-(1-progress)**3);
        if(progress<1)selectionFrame=requestAnimationFrame(tick);
      };
      if(!reduced.matches)selectionFrame=requestAnimationFrame(tick);
    }
    const currentClip=getComputedStyle(dialog).clipPath;
    const fromClip=currentClip==='none'?'inset(0px round 0px)':currentClip;
    frameAnimation?.cancel();
    if(!reduced.matches)frameAnimation=dialog.animate([{clipPath:fromClip},{clipPath:clip()}],{duration:200,easing:'cubic-bezier(.2,0,0,1)'});
    animateImage(from,()=>{
      if(selectionIndex!==undefined)track.scrollLeft=selectionIndex*track.clientWidth;
      cancelSelection();restoreFrame();scale=1;x=0;y=0;paint();changed();done?.();
    });
  };
  const reset=()=>{
    animation?.cancel();animation=undefined;swipe.cancel();cancelSelection();
    if(target)target.style.removeProperty('transform');
    for(const id of points.keys())if(track.hasPointerCapture(id))track.releasePointerCapture(id);
    points.clear();target=undefined;scale=1;x=0;y=0;origin=undefined;axis=undefined;owned=false;lastTap=0;
    restoreFrame();changed();
  };
  const consume=(event:PointerEvent)=>{event.preventDefault();event.stopImmediatePropagation();};
  track.addEventListener('pointerdown',event=>{
    if(!mobile.matches||event.pointerType!=='touch'||!dialog.open)return;
    if(selectionIndex!==undefined){consume(event);return;}
    if(!points.size){swipe.cancel(true);beforeGesture();}
    target=image();if(!target?.naturalWidth||target.style.visibility==='hidden')return;
    interruptAnimation();
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(points.size===1){origin={x:event.clientX,y:event.clientY};axis=undefined;moved=false;owned=!!fitted;samples=[{x:event.clientX,time:performance.now()}];}
    if(points.size>1){
      swipe.cancel();track.scrollLeft=Math.round(track.scrollLeft/track.clientWidth)*track.clientWidth;
      owned=true;moved=true;delete dialog.dataset.viewerZoomControls;
      dialog.dispatchEvent(new Event('viewerinteractionstart'));
    }
    rebase();track.setPointerCapture(event.pointerId);
    if(owned)consume(event);
  },{signal,capture:true});
  track.addEventListener('pointermove',event=>{
    if(!points.has(event.pointerId)||!origin)return;
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    const dx=event.clientX-origin.x,dy=event.clientY-origin.y,now=performance.now();
    if(Math.hypot(dx,dy)>8)moved=true;
    if(!owned){
      if(!axis&&moved){
        axis=Math.abs(dx)>Math.abs(dy)?'horizontal':'vertical';
        if(axis==='horizontal'){dialog.dispatchEvent(new Event('viewerinteractionstart'));swipe.begin();}
      }
      if(axis==='horizontal'){
        consume(event);swipe.move(dx);samples.push({x:event.clientX,time:now});
        while(samples.length>2&&samples[0].time<now-100)samples.shift();
      }
      return;
    }
    consume(event);if(moved)delete dialog.dataset.viewerZoomControls;
    const next=Math.max(1,Math.min(4,base.scale*distance()/base.distance));
    // A pinch inward from fit is a no-op. Expansion needs deliberate enlargement.
    if(!fitted&&next<1.06){scale=1;x=0;y=0;paint();return;}
    expand();
    const c=center(),rect=track.getBoundingClientRect(),stage=stageCenter();
    ({scale,x,y}=zoomBounds(next,c.x-stage.x-base.anchor.x*next,c.y-stage.y-base.anchor.y*next,
      fitted!.width,fitted!.height,rect.width,rect.height,true));
    paint();
  },{signal,capture:true,passive:false});
  const release=(event:PointerEvent)=>{
    if(!points.has(event.pointerId))return;
    const wasOwned=owned;points.delete(event.pointerId);
    if(track.hasPointerCapture(event.pointerId))track.releasePointerCapture(event.pointerId);
    if(wasOwned||axis==='horizontal'){consume(event);ignoreUntil=performance.now()+450;}
    if(points.size){rebase();return;}
    const now=performance.now();let doubleTap=false;
    if(event.type!=='pointercancel'&&!moved&&origin){
      doubleTap=now-lastTap<300&&!!lastPoint&&Math.hypot(origin.x-lastPoint.x,origin.y-lastPoint.y)<32;
      if(doubleTap){
        if(fitted)returnToFit();else{
          const previous=stageCenter(),fromScale=scale;
          expand();const stage=stageCenter(),rect=track.getBoundingClientRect();
          const from=`translate(${previous.x-stage.x}px,${previous.y-stage.y}px) scale(${fromScale})`;
          ({scale,x,y}=zoomBounds(2.5,(origin.x-stage.x)-(origin.x-previous.x)*2.5,(origin.y-stage.y)-(origin.y-previous.y)*2.5,
            fitted!.width,fitted!.height,rect.width,rect.height,true));
          animateImage(from);
        }
        ignoreUntil=now+450;lastTap=0;
      }else{
        lastTap=now;lastPoint=origin;
        if(wasOwned&&fitted){if('viewerZoomControls' in dialog.dataset)delete dialog.dataset.viewerZoomControls;else dialog.dataset.viewerZoomControls='';}
      }
    }else lastTap=0;
    const horizontal=axis==='horizontal'&&!wasOwned;
    const dx=origin?event.clientX-origin.x:0,first=samples[0],last=samples.at(-1);
    const elapsed=first?now-first.time:0,velocity=elapsed>0&&last&&now-last.time<=100?(last.x-first.x)/elapsed:0;
    owned=false;origin=undefined;axis=undefined;
    if(!doubleTap&&fitted&&scale<=1.04)returnToFit();
    else{
      if(wasOwned||doubleTap||moved)changed();
    }
    if(horizontal)swipe.finish(dx,velocity,event.type==='pointercancel');
  };
  window.addEventListener('pointerup',release,{signal,capture:true,passive:false});
  window.addEventListener('pointercancel',release,{signal,capture:true,passive:false});
  track.addEventListener('click',event=>{if(fitted||performance.now()<ignoreUntil){event.preventDefault();event.stopImmediatePropagation();}},{signal,capture:true});
  dialog.addEventListener('click',event=>{
    if(fitted&&!('viewerZoomControls' in dialog.dataset&&(event.target as Element)?.closest?.('button'))){event.preventDefault();event.stopImmediatePropagation();}
  },{signal,capture:true});
  for(const event of ['blur','pagehide','pageshow','resize'])window.addEventListener(event,reset,{signal});
  dialog.addEventListener('close',reset,{signal});mobile.addEventListener('change',reset,{signal});
  signal.addEventListener('abort',reset,{once:true});
  return Object.assign(reset,{preview:(index:number,current:number)=>{
    if(!fitted)return false;
    beforeGesture();interruptAnimation();
    returnToFit(()=>select(index),index===current&&track.scrollLeft===index*track.clientWidth?undefined:index);
    return true;
  }});
}
