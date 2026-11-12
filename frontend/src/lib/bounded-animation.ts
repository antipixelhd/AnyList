export function boundedAnimation(element: HTMLElement, frames: Keyframe[] | PropertyIndexedKeyframes, duration: number) {
  const animation = element.animate(frames, {duration, easing:'cubic-bezier(.2,.7,.2,1)'});
  if(element.ownerDocument.timeline.currentTime!==null)animation.startTime=element.ownerDocument.timeline.currentTime;
  let resolve!:()=>void, settled=false;
  const finished=new Promise<void>(done=>{resolve=done;});
  const cancel=()=>{
    if(settled)return;
    settled=true;clearTimeout(timer);animation.cancel();resolve();
  };
  const timer=setTimeout(cancel,duration+80);
  void animation.finished.then(cancel,cancel);
  return {finished,cancel};
}
