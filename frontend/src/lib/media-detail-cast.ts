import { createContentTransition } from './content-motion';
import { detailCastPresentation, type DetailPerson } from './media-detail-data';
import { responsiveArtwork } from './responsive-artwork';

export function mountDetailCast(root: HTMLElement, signal: AbortSignal) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Set<Animation>();
  const stop = () => { animations.forEach(animation => animation.cancel()); animations.clear(); };
  reduced.addEventListener('change',stop,{signal});
  signal.addEventListener('abort',stop,{once:true});
  const containers = [...root.querySelectorAll<HTMLElement>('[data-cast-content]')];
  const transitions = new Map(containers.map(container=>[container,createContentTransition(container,signal)]));
  const signatures = new WeakMap<HTMLElement,string>();
  const pending = new WeakMap<HTMLElement,()=>void>();
  let mode: 'actors' | 'characters' = 'actors';

  function artwork(card: HTMLElement, urls: string[]) {
    const holder=card.querySelector<HTMLElement>('.media-person-art')!;
    const placeholder=holder.querySelector<HTMLElement>('.lab-person-initials')!;
    const signature=JSON.stringify(urls);
    if(signatures.get(card)===signature)return;
    signatures.set(card,signature);
    pending.get(holder)?.();
    holder.querySelectorAll('[data-cast-image-pending]').forEach(image=>image.remove());
    let current=holder.querySelector<HTMLImageElement>('img');
    if(current)current.onerror=null;
    let index=0;
    const active=()=>!signal.aborted&&signatures.get(card)===signature;
    const next=()=>{
      if(!active())return;
      const art=responsiveArtwork(urls[index++],{size:'w185',sizes:'57px',route:'direct'});
      if(!art){current?.remove();current=null;placeholder.hidden=false;return;}
      if(current?.getAttribute('src')===art.src&&(!current.complete||current.naturalWidth>0)) {
        current.onerror=next;placeholder.hidden=true;return;
      }
      const candidate=document.createElement('img');candidate.alt='';candidate.loading='lazy';candidate.dataset.castImagePending='';candidate.style.opacity='0';
      const cleanup=()=>{candidate.onload=null;candidate.onerror=null;signal.removeEventListener('abort',cancel);if(pending.get(holder)===cancel)pending.delete(holder);};
      const cancel=()=>{cleanup();candidate.remove();};
      pending.set(holder,cancel);signal.addEventListener('abort',cancel,{once:true});
      candidate.onerror=()=>{cleanup();candidate.remove();next();};
      candidate.onload=()=>{
        cleanup();
        if(!active()||!card.isConnected){candidate.remove();return;}
        current?.remove();current=candidate;placeholder.hidden=true;delete candidate.dataset.castImagePending;candidate.style.removeProperty('opacity');
        if(!reduced.matches){const animation=candidate.animate([{opacity:0},{opacity:1}],{duration:240,easing:'ease-out'});animations.add(animation);void animation.finished.catch(()=>{}).then(()=>animations.delete(animation));}
      };
      holder.prepend(candidate);
      if(art.srcset)candidate.srcset=art.srcset;if(art.sizes)candidate.sizes=art.sizes;candidate.src=art.src;
    };
    next();
  }
  const paint=(container: HTMLElement, selected: typeof mode)=>{
    container.querySelectorAll<HTMLElement>('[data-cast-person]').forEach(card=>{
      const person=JSON.parse(card.dataset.castPerson!) as DetailPerson;
      const view=detailCastPresentation(person,selected);
      card.querySelector<HTMLElement>('[data-cast-primary]')!.textContent=view.primary;
      const secondary=card.querySelector<HTMLElement>('[data-cast-secondary]')!;
      secondary.textContent=view.secondary;secondary.hidden=!view.secondary;
      artwork(card,view.images);
    });
  };
  root.querySelectorAll<HTMLButtonElement>('[data-cast-mode]').forEach(control=>control.setAttribute('aria-pressed',String(control.dataset.castMode===mode)));
  containers.forEach(container=>paint(container,mode));
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-cast-mode]');
    if(!button||button.dataset.castMode===mode)return;
    mode=button.dataset.castMode==='characters'?'characters':'actors';
    root.querySelectorAll<HTMLButtonElement>('[data-cast-mode]').forEach(control=>control.setAttribute('aria-pressed',String(control.dataset.castMode===mode)));
    const selected=mode;
    transitions.forEach((transition,container)=>void transition(()=>paint(container,selected)));
  },{signal});
}
