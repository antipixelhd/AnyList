/** Keep horizontal swipes native; own only a single-finger downward drag. */
export function bindDetailImageDragDismiss(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal) {
  const mobile=matchMedia('(max-width:650px), (max-height:650px)');
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  let unlock:(()=>void)|undefined;
  let gesture:{id:number;x:number;y:number;axis?:'horizontal'|'vertical';distance:number;samples:{y:number;time:number}[]}|undefined;
  const pointers=new Set<number>();
  let settling:ReturnType<typeof setTimeout>|undefined,ignoreClickUntil=0;

  const reset=()=>{
    clearTimeout(settling);settling=undefined;
    const pointer=gesture?.id;gesture=undefined;
    if(pointer!==undefined&&track.hasPointerCapture?.(pointer))track.releasePointerCapture(pointer);
    delete dialog.dataset.viewerDragging;delete dialog.dataset.viewerSettling;
    dialog.style.removeProperty('translate');dialog.style.removeProperty('--viewer-backdrop');
  };
  const lockPage=()=>{
    if(unlock)return;
    const x=window.scrollX,y=window.scrollY;
    const body=document.body,html=document.documentElement;
    const saved=[...['position','top','left','right','width','overflow'].map(property=>({element:body,property,value:body.style.getPropertyValue(property),priority:body.style.getPropertyPriority(property)})),
      {element:html,property:'overflow',value:html.style.getPropertyValue('overflow'),priority:html.style.getPropertyPriority('overflow')}];
    body.style.position='fixed';body.style.top=`${-y}px`;body.style.left=`${-x}px`;body.style.right='0';body.style.width='100%';body.style.overflow='hidden';html.style.overflow='hidden';
    unlock=()=>{
      saved.forEach(({element,property,value,priority})=>value?element.style.setProperty(property,value,priority):element.style.removeProperty(property));
      window.scrollTo({left:x,top:y,behavior:'instant'});unlock=undefined;
    };
  };
  const settle=(close=false)=>{
    gesture=undefined;delete dialog.dataset.viewerDragging;
    dialog.dataset.viewerSettling='';
    dialog.style.translate=close?`0 ${window.innerHeight}px`:'0 0';
    dialog.style.setProperty('--viewer-backdrop',close?'0':'.8');
    clearTimeout(settling);
    const finish=()=>{if(close&&dialog.open)dialog.close();reset();};
    if(reduced.matches)finish();else settling=setTimeout(finish,240);
  };
  track.addEventListener('pointerdown',event=>{
    if(!mobile.matches||!dialog.open||dialog.hasAttribute('data-motion-closing'))return;
    pointers.add(event.pointerId);
    if(pointers.size>1){settle();return;}
    if(settling||(window.visualViewport?.scale||1)>1)return;
    gesture={id:event.pointerId,x:event.clientX,y:event.clientY,distance:0,samples:[{y:event.clientY,time:performance.now()}]};
  },{signal,passive:true});
  track.addEventListener('pointermove',event=>{
    if('viewerZoomed' in dialog.dataset){reset();return;}
    if(!gesture||gesture.id!==event.pointerId||pointers.size!==1)return;
    const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y;
    const now=performance.now();
    gesture.samples.push({y:event.clientY,time:now});
    while(gesture.samples.length>2&&gesture.samples[0].time<now-100)gesture.samples.shift();
    if(!gesture.axis){
      if(Math.max(Math.abs(dx),Math.abs(dy))<8)return;
      gesture.axis=Math.abs(dy)>=Math.abs(dx)?'vertical':'horizontal';
    }
    if(gesture.axis!=='vertical')return;
    event.preventDefault();
    track.setPointerCapture(event.pointerId);
    gesture.distance=Math.max(0,dy);
    dialog.dataset.viewerDragging='';
    dialog.style.translate=`0 ${gesture.distance}px`;
    dialog.style.setProperty('--viewer-backdrop',String(.8*Math.max(0,1-gesture.distance/window.innerHeight)));
  },{signal,passive:false});
  const release=(event:PointerEvent)=>{
    pointers.delete(event.pointerId);
    if(!gesture||gesture.id!==event.pointerId)return;
    if(gesture.axis==='vertical'){
      ignoreClickUntil=performance.now()+400;
      const threshold=Math.min(112,Math.max(72,dialog.clientHeight*.12));
      const first=gesture.samples[0],last=gesture.samples.at(-1)!;
      const elapsed=performance.now()-first.time;
      const velocity=elapsed>0&&performance.now()-last.time<=100?(last.y-first.y)/elapsed:0;
      // Material's default significant velocity is 500 px/s. Adapt that
      // pattern to CSS pixels, with minimum travel to reject accidental taps.
      // https://github.com/material-components/material-components-android/blob/master/lib/java/com/google/android/material/bottomsheet/BottomSheetBehavior.java
      const flick=gesture.distance>=32&&velocity>=.5;
      settle(event.type!=='pointercancel'&&velocity>=-.2&&(gesture.distance>=threshold||flick));
    }else gesture=undefined;
  };
  window.addEventListener('pointerup',release,{signal,passive:true});
  window.addEventListener('pointercancel',release,{signal,passive:true});
  // Native back/forward and OS interruptions can omit pointerup/cancel. A
  // bfcache restore retains these listeners and variables, including stale
  // pointer IDs and pending settle timers, so reset without remounting images.
  const interrupt=()=>{reset();pointers.clear();ignoreClickUntil=0;};
  dialog.addEventListener('viewerzoomstart',interrupt,{signal});
  window.addEventListener('blur',interrupt,{signal});
  window.addEventListener('pagehide',()=>{interrupt();unlock?.();},{signal});
  window.addEventListener('pageshow',()=>{interrupt();if(dialog.open)lockPage();else unlock?.();},{signal});
  track.addEventListener('click',event=>{
    if(performance.now()<ignoreClickUntil){event.preventDefault();event.stopImmediatePropagation();}
  },{signal,capture:true});
  dialog.addEventListener('close',()=>{reset();pointers.clear();unlock?.();},{signal});
  mobile.addEventListener('change',()=>{reset();pointers.clear();if(dialog.open)lockPage();else unlock?.();},{signal});
  signal.addEventListener('abort',()=>{if(dialog.open)dialog.close();reset();unlock?.();},{once:true});
  return ()=>{reset();pointers.clear();lockPage();};
}
