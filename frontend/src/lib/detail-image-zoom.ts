import {createDetailImageSwipe} from './detail-image-swipe';
import {createGallerySpring,gallerySpatial} from './gallery-spring';

type Point={x:number;y:number};
export function zoomBounds(scale:number,x:number,y:number,width:number,height:number,viewportWidth=width,viewportHeight=height,keepAnchor=false,anchor?:Point) {
  scale=Math.max(1,Math.min(4,scale));
  const clamp=(value:number,limit:number)=>limit>0?Math.max(-limit,Math.min(limit,value)):0;
  const limit=(size:number,viewport:number,held=0)=>Math.max(Math.abs(held),keepAnchor?Math.abs(size*scale-viewport)/2:Math.max(0,(size*scale-viewport)/2));
  return {scale,x:clamp(x,limit(width,viewportWidth,anchor?.x)),y:clamp(y,limit(height,viewportHeight,anchor?.y))};
}

/** A contact sequence owns exactly one gesture: swipe/dismiss at fit, or pinch/pan. */
export function bindDetailImageZoom(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal,image:()=>HTMLImageElement|undefined,select:(index:number,preserveScroll?:boolean,preserveZoom?:boolean)=>void,changed:()=>void,beforeGesture:()=>void=()=>{}) {
  const mobile=matchMedia('(max-width:650px), (max-height:650px)');
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  const swipe=createDetailImageSwipe(track,signal,select);
  const slots=dialog.querySelector<HTMLElement>('[data-image-viewer-slots]');
  const chrome=createGallerySpring({x:0,y:0},value=>{
    if(slots){
      if(value.x||value.y){slots.style.transform=`translate(${value.x}px,${value.y}px)`;dialog.dataset.viewerChromeMoving='';}
      else {slots.style.removeProperty('transform');delete dialog.dataset.viewerChromeMoving;}
    }
  },signal,{stiffness:gallerySpatial.fast});
  const preserveChrome=()=>{
    chrome.stop();const before=slots?.getBoundingClientRect();chrome.snap({x:0,y:0});
    return ()=>{
      if(!slots||!before)return;
      const after=slots.getBoundingClientRect();chrome.snap({x:before.left-after.left,y:before.top-after.top});
      chrome.to({x:0,y:0},{immediate:reduced.matches||getComputedStyle(slots).visibility==='hidden'});
    };
  };
  const points=new Map<number,Point>();
  let target:HTMLImageElement|undefined,scale=1,x=0,y=0;
  let fitted:{width:number;height:number;center:Point}|undefined;

  let selectionIndex:number|undefined,adoptedIndex:number|undefined,incoming:HTMLImageElement|undefined;
  const cancelSelection=()=>{
    selectionIndex=undefined;
    delete track.dataset.viewerSwiping;
    if(incoming!==target)incoming?.style.removeProperty('transform');incoming=undefined;
  };
  let origin:Point|undefined,axis:'horizontal'|'vertical'|undefined,moved=false,owned=false;
  let ignoreUntil=0,lastTap=0,lastPoint:Point|undefined,samples:{x:number;time:number}[]=[];
  let pagingInterrupted=false;
  let base={scale:1,distance:1,center:{x:0,y:0},anchor:{x:0,y:0}};
  const center=()=>{const rows=[...points.values()];return rows.length>1?{x:(rows[0].x+rows[1].x)/2,y:(rows[0].y+rows[1].y)/2}:rows[0];};
  const distance=()=>{const rows=[...points.values()];return rows.length>1?Math.max(1,Math.hypot(rows[1].x-rows[0].x,rows[1].y-rows[0].y)):1;};
  const stageCenter=()=>{const r=track.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};};
  const transform=()=>`translate(${x}px,${y}px) scale(${scale})`;
  const paint=()=>{if(target)target.style.transform=transform();};
  const motion=createGallerySpring({x:0,y:0,scale:1,left:0},value=>{
    ({x,y,scale}=value);paint();
    if(selectionIndex!==undefined)track.scrollLeft=value.left;
  },signal,{precision:{scale:.0002},bounds:()=>({scale:{min:1,max:4},left:{min:0,max:Math.max(0,(track.children.length-1)*track.clientWidth)}})});
  const direct=()=>motion.drag({x,y,scale,left:track.scrollLeft});
  reduced.addEventListener('change',()=>{if(reduced.matches){motion.finish();chrome.finish();}},{signal});
  const restoreFrame=()=>{
    const expanded=!!fitted,index=Math.round(track.scrollLeft/Math.max(1,track.clientWidth));
    fitted=undefined;dialog.style.removeProperty('clip-path');
    for(const property of ['--viewer-zoom-width','--viewer-zoom-height'])dialog.style.removeProperty(property);
    delete dialog.dataset.viewerZoomFrame;delete dialog.dataset.viewerZoomed;delete dialog.dataset.viewerZoomControls;
    if(expanded)track.scrollLeft=index*track.clientWidth;
  };
  const expand=()=>{
    if(fitted)return;
    const finishChrome=preserveChrome();
    const index=Math.round(track.scrollLeft/Math.max(1,track.clientWidth));
    const rect=track.getBoundingClientRect();
    fitted={width:rect.width,height:rect.height,center:stageCenter()};
    dialog.style.setProperty('--viewer-zoom-width',`${rect.width}px`);
    dialog.style.setProperty('--viewer-zoom-height',`${rect.height}px`);
    dialog.dataset.viewerZoomFrame='';dialog.dataset.viewerZoomed='';
    track.scrollLeft=index*track.clientWidth;finishChrome();
    // The invisible interaction surface changes once. The same rounded photo
    // stays under the fingers; no timed aperture competes with direct scaling.
  };
  const rebase=()=>{
    const c=center()||{x:0,y:0},stage=stageCenter();
    base={scale,distance:distance(),center:c,anchor:{x:(c.x-stage.x-x)/scale,y:(c.y-stage.y-y)/scale}};
  };
  const interruptAnimation=()=>{motion.stop();cancelSelection();};
  const returnToFit=(done?:()=>void,index?:number,gesture=false,preview=false)=>{
    if(!fitted){scale=1;x=0;y=0;paint();changed();done?.();return;}
    const stage=stageCenter(),destination={scale:1,x:fitted.center.x-stage.x,y:fitted.center.y-stage.y,
      left:index===undefined?track.scrollLeft:index*track.clientWidth};
    if(index!==undefined){
      selectionIndex=index;track.dataset.viewerSwiping='';
      incoming=track.children[index]?.querySelector<HTMLImageElement>('img')||undefined;
      if(incoming&&incoming!==target)incoming.style.transform=`translate(${destination.x}px,${destination.y}px)`;
    }
    motion.to(destination,{gesture,stiffness:preview?gallerySpatial.fast:gallerySpatial.default,immediate:reduced.matches,done:()=>{
      if(selectionIndex!==undefined)track.scrollLeft=selectionIndex*track.clientWidth;
      const finishChrome=preserveChrome(),adopted=adoptedIndex;adoptedIndex=undefined;
      cancelSelection();restoreFrame();motion.snap({x:0,y:0,scale:1,left:track.scrollLeft});changed();
      if(done)done();else if(adopted!==undefined)select(adopted);finishChrome();
    }});
  };
  const clear=(preserveContact=false)=>{
    motion.stop();swipe.cancel();cancelSelection();chrome.snap({x:0,y:0});
    if(target)target.style.removeProperty('transform');
    for(const id of points.keys())if(track.hasPointerCapture(id))track.releasePointerCapture(id);
    points.clear();target=undefined;scale=1;x=0;y=0;origin=undefined;axis=undefined;owned=false;lastTap=0;adoptedIndex=undefined;pagingInterrupted=false;
    if(!preserveContact)delete track.dataset.viewerContact;
    restoreFrame();motion.snap({x:0,y:0,scale:1,left:track.scrollLeft});changed();
  };
  const reset=()=>clear();
  const consume=(event:PointerEvent)=>{event.preventDefault();event.stopImmediatePropagation();};
  track.addEventListener('pointerdown',event=>{
    if(!mobile.matches||event.pointerType!=='touch'||!dialog.open)return;
    track.dataset.viewerContact='';
    if(selectionIndex!==undefined&&fitted){
      // Adopt the photo currently under the hand without waiting for the
      // preview spring, or snapping the camera to a different slide first.
      motion.stop();
      const width=track.clientWidth,oldIndex=[...track.children].findIndex(slide=>slide.querySelector('img')===target);
      let index=Math.max(0,Math.min(track.children.length-1,Math.round(track.scrollLeft/width)));
      let chosen=track.children[index]?.querySelector<HTMLImageElement>('img')||undefined;
      if(!chosen?.naturalWidth||chosen.style.visibility==='hidden'){index=oldIndex;chosen=target;}
      const stage=stageCenter(),nextScale=chosen===target?scale:1;
      const nextX=(chosen===target?x:fitted.center.x-stage.x)+index*width-track.scrollLeft;
      const nextY=chosen===target?y:fitted.center.y-stage.y;
      cancelSelection();if(target!==chosen)target?.style.removeProperty('transform');
      select(index,true,true);target=chosen;adoptedIndex=index;
      track.scrollLeft=index*width;motion.snap({x:nextX,y:nextY,scale:nextScale,left:track.scrollLeft});
    }
    if(!points.size){pagingInterrupted=swipe.cancel(true);beforeGesture();}
    target=image();if(!target?.naturalWidth||target.style.visibility==='hidden')return;
    interruptAnimation();
    points.set(event.pointerId,{x:event.clientX,y:event.clientY});
    track.dataset.viewerContact='';
    if(points.size===1){origin={x:event.clientX,y:event.clientY};axis=undefined;moved=false;owned=!!fitted;samples=[{x:event.clientX,time:performance.now()}];}
    if(points.size>1){
      const index=[...track.children].findIndex(slide=>slide.querySelector('img')===target);
      const offset=index*track.clientWidth-track.scrollLeft;
      swipe.cancel();track.scrollLeft=index*track.clientWidth;x+=offset;
      motion.snap({x,y,scale,left:track.scrollLeft});pagingInterrupted=false;
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
    // Inward scaling at fit is a no-op. Outward scaling must remain continuous:
    // discarding a percentage first creates a visible jump at that threshold.
    if(!fitted&&next<=1){scale=1;paint();return;}
    const expanding=!fitted;expand();
    if(expanding)motion.snap({x,y,scale,left:track.scrollLeft});
    const c=center(),rect=track.getBoundingClientRect(),stage=stageCenter();
    ({scale,x,y}=zoomBounds(next,c.x-stage.x-base.anchor.x*next,c.y-stage.y-base.anchor.y*next,
      fitted!.width,fitted!.height,rect.width,rect.height,true,
      {x:base.center.x-stage.x-base.anchor.x*next,y:base.center.y-stage.y-base.anchor.y*next}));
    direct();
  },{signal,capture:true,passive:false});
  const release=(event:PointerEvent)=>{
    if(!points.has(event.pointerId))return;
    const wasOwned=owned;points.delete(event.pointerId);
    if(track.hasPointerCapture(event.pointerId))track.releasePointerCapture(event.pointerId);
    if(wasOwned||axis==='horizontal'){consume(event);ignoreUntil=performance.now()+450;}
    if(points.size){rebase();return;}
    delete track.dataset.viewerContact;
    const now=performance.now();let doubleTap=false;
    if(event.type!=='pointercancel'&&!moved&&origin){
      doubleTap=now-lastTap<300&&!!lastPoint&&Math.hypot(origin.x-lastPoint.x,origin.y-lastPoint.y)<32;
      if(doubleTap){
        if(fitted)returnToFit();else{
          const previous=stageCenter(),fromScale=scale;
          expand();const stage=stageCenter(),rect=track.getBoundingClientRect();
          motion.snap({x:previous.x-stage.x,y:previous.y-stage.y,scale:fromScale,left:track.scrollLeft});
          const destination=zoomBounds(2.5,(origin.x-stage.x)-(origin.x-previous.x)*2.5,(origin.y-stage.y)-(origin.y-previous.y)*2.5,
            fitted!.width,fitted!.height,rect.width,rect.height,true);
          motion.to({...destination,left:track.scrollLeft},{immediate:reduced.matches});
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
    if(!doubleTap&&fitted&&scale<=1.04)returnToFit(undefined,undefined,true);
    else if(!fitted&&wasOwned&&(x||y))motion.to({x:0,y:0,scale:1,left:track.scrollLeft},{gesture:true,immediate:reduced.matches,done:changed});
    else{
      if(wasOwned||doubleTap||moved)changed();
    }
    if(horizontal)swipe.finish(dx,velocity,event.type==='pointercancel');
    else if(pagingInterrupted&&!wasOwned)swipe.finish(0,0);
    pagingInterrupted=false;
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
  return Object.assign(reset,{preserveInteraction:()=>clear(true),preview:(index:number,current:number)=>{
    if(!fitted)return false;
    beforeGesture();interruptAnimation();
    returnToFit(()=>select(index),index===current&&track.scrollLeft===index*track.clientWidth?undefined:index,false,true);
    return true;
  }});
}
