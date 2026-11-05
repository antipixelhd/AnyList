import {pinnedRailTarget} from './pinned-rail-data';
/** Direction-aware, bounded rails. Only the development preview opts in. */
export function mountPinnedSidebars(root:HTMLElement,signal:AbortSignal) {
  const desktop=matchMedia('(min-width: 1101px)');
  const rails=[...root.querySelectorAll<HTMLElement>('.lab-sidebar,.lab-secondary-column,.contributor-sidebar')].map(track=>{
    const content=document.createElement('div');content.className='pinned-rail-content';
    content.append(...track.childNodes);track.append(content);track.classList.add('pinned-rail');
    return {track,content,target:0};
  });
  let frame=0;
  const tick=()=>{
    frame=0;
    for(const rail of rails){
      const {track,content}=rail;
      if(!desktop.matches||!track.getClientRects().length){rail.target=0;content.style.removeProperty('transform');continue;}
      const origin=track.getBoundingClientRect().top, height=content.offsetHeight;
      rail.target=pinnedRailTarget(origin,height,track.clientHeight,innerHeight,rail.target);
      content.style.transform=`translate3d(0,${rail.target}px,0)`;
    }
  };
  const update=()=>{if(!frame&&!signal.aborted)frame=requestAnimationFrame(tick);};
  const observer=new ResizeObserver(update);observer.observe(root);
  rails.forEach(({track,content})=>{observer.observe(track);observer.observe(content);});
  window.addEventListener('scroll',update,{passive:true,signal});window.addEventListener('resize',update,{signal});
  desktop.addEventListener('change',update,{signal});
  signal.addEventListener('abort',()=>{
    cancelAnimationFrame(frame);observer.disconnect();
    rails.forEach(({track,content})=>{content.replaceWith(...content.childNodes);track.classList.remove('pinned-rail');});
  },{once:true});update();
}

let lifetime:AbortController|undefined;
function initialize(){lifetime?.abort();const root=document.querySelector<HTMLElement>('[data-pinned-preview]');if(root){lifetime=new AbortController();mountPinnedSidebars(root,lifetime.signal);}}
document.addEventListener('astro:page-load',initialize);
document.addEventListener('astro:before-swap',()=>lifetime?.abort());
initialize();
