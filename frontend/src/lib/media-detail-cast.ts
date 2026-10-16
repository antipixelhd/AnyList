import { createContentTransition } from './content-motion';
import { detailCastPresentation, detailCredits, detailCreators, detailCreditEpisodes, type DetailPerson, type DetailMedia } from './media-detail-data';
import { responsiveArtwork } from './responsive-artwork';
import { type DetailLoadState } from './detail-background';
import { detailPeopleLoading, waitForDetailPortraits } from './detail-people-loading';
import {createProgressiveCredits} from './progressive-credits';

export function mountDetailCast(root: HTMLElement, signal: AbortSignal, info:any = {}) {
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
  let metadataPending=!!info.backgroundAvailable&&info.characterStatus?.state!=='ready',error='';
  const statuses=[...root.querySelectorAll<HTMLElement>('[data-detail-people-status]')];
  const status=()=>statuses.forEach(node=>{
    node.hidden=!error;
    node.replaceChildren(document.createTextNode(error));
    if(error){const button=document.createElement('button');button.type='button';button.className='lab-text-button';button.textContent='Retry';button.dataset.detailPeopleRetry='';node.append(' ',button);}
  });
  const warmed=new Set<string>();
  const warm=()=>{
    if(mode!=='actors'||signal.aborted)return;
    root.querySelectorAll<HTMLElement>('[data-credit-preview] [data-cast-person]').forEach(card=>{
      const person=JSON.parse(card.dataset.castPerson!) as DetailPerson;
      const art=responsiveArtwork(person.character_image,{size:'w185',sizes:'57px',route:'auto'});
      if(!art||warmed.has(art.src))return;
      warmed.add(art.src);
      const image=new Image();image.fetchPriority='low';image.onerror=()=>warmed.delete(art.src);
      if(art.srcset)image.srcset=art.srcset;if(art.sizes)image.sizes=art.sizes;image.src=art.src;
    });
  };

  function artwork(card: HTMLElement, urls: string[], waiting=false, character=false) {
    const holder=card.querySelector<HTMLElement>('.media-person-art')!;
    const placeholder=holder.querySelector<HTMLElement>('.lab-person-initials')!;
    const signature=JSON.stringify([urls,waiting,character]);
    if(signatures.get(card)===signature)return;
    signatures.set(card,signature);
    pending.get(holder)?.();
    holder.querySelectorAll('[data-cast-image-pending]').forEach(image=>image.remove());
    let current=holder.querySelector<HTMLImageElement>('img');
    if(current)current.onerror=null;
    if(waiting){holder.dataset.castLoading='';if(current)current.hidden=true;placeholder.hidden=true;return;}
    let index=0;
    const active=()=>!signal.aborted&&signatures.get(card)===signature;
    const next=()=>{
      if(!active())return;
      const art=responsiveArtwork(urls[index++],{size:'w185',sizes:'57px',route:'auto'});
      if(!art){current?.remove();current=null;placeholder.hidden=false;delete holder.dataset.castLoading;return;}
      if(current?.getAttribute('src')===art.src&&(!current.complete||current.naturalWidth>0)) {
        current.onerror=next;current.hidden=false;placeholder.hidden=true;delete holder.dataset.castLoading;return;
      }
      if(character){holder.dataset.castLoading='';if(current)current.hidden=true;placeholder.hidden=true;}
      const primary=!!card.closest('[data-credit-preview]')&&!character;
      const candidate=document.createElement('img');candidate.alt='';candidate.loading=primary?'eager':'lazy';candidate.fetchPriority=primary?'auto':'low';candidate.dataset.castImagePending='';candidate.style.opacity='0';
      const cleanup=()=>{candidate.onload=null;candidate.onerror=null;signal.removeEventListener('abort',cancel);if(pending.get(holder)===cancel)pending.delete(holder);};
      const cancel=()=>{cleanup();candidate.remove();};
      pending.set(holder,cancel);signal.addEventListener('abort',cancel,{once:true});
      candidate.onerror=()=>{cleanup();candidate.remove();next();};
      candidate.onload=()=>{
        cleanup();
        if(!active()||!card.isConnected){candidate.remove();return;}
        current?.remove();current=candidate;placeholder.hidden=true;delete holder.dataset.castLoading;delete candidate.dataset.castImagePending;candidate.style.removeProperty('opacity');
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
      const hasCharacter=!!(person.character_image||person.character_images?.length);
      artwork(card,view.images,selected==='characters'&&metadataPending&&!hasCharacter,selected==='characters'&&hasCharacter);
    });
  };
  root.querySelectorAll<HTMLButtonElement>('[data-cast-mode]').forEach(control=>control.setAttribute('aria-pressed',String(control.dataset.castMode===mode)));
  containers.forEach(container=>paint(container,mode));
  status();

  const fill=(container:HTMLElement,people:DetailPerson[])=>{
    const old=new Map([...container.querySelectorAll<HTMLElement>('[data-person-id]')].map(card=>[`${card.dataset.personKind}:${card.dataset.personId}`,card]));
    const template=root.querySelector<HTMLTemplateElement>('[data-detail-person-template]')!;
    const cards=people.map(person=>{
      const card=old.get(`${person.kind}:${person.id}`)||template.content.firstElementChild!.cloneNode(true) as HTMLElement;
      card.dataset.personId=String(person.id);card.dataset.personKind=person.kind;
      if(person.url)card.setAttribute('href',person.url);else card.removeAttribute('href');
      if(person.kind==='actor')card.dataset.castPerson=JSON.stringify(person);else delete card.dataset.castPerson;
      card.querySelector<HTMLElement>('h3')!.textContent=person.name;
      card.querySelector<HTMLElement>('p')!.textContent=person.characters.join(' / ')||person.roles.join(', ');
      const episodes=detailCreditEpisodes(person);
      let count=card.querySelector<HTMLElement>('[data-credit-episodes]');
      if(episodes){
        if(!count){count=document.createElement('span');count.className='media-credit-episodes';count.dataset.creditEpisodes='';card.querySelector('h3')!.parentElement!.append(count);}
        count.textContent=episodes;
      }else count?.remove();
      if(person.kind!=='actor'&&card.querySelector('.media-person-art'))artwork(card,[person.image].filter((url):url is string=>!!url));
      return card;
    });
    // Reuse portraits and cards already on screen instead of reloading them.
    cards.forEach((card,index)=>{if(container.children[index]!==card)container.insertBefore(card,container.children[index]||null);});
    [...container.children].filter(card=>!cards.includes(card as HTMLElement)).forEach(card=>card.remove());
  };
  const deferred=new WeakMap<HTMLElement,ReturnType<typeof createProgressiveCredits<DetailPerson>>>();
  root.querySelectorAll<HTMLElement>('[data-credit-section]').forEach(section=>{
    const data=section.querySelector('[data-credit-deferred]');
    const list=createProgressiveCredits<DetailPerson>(section.querySelector<HTMLElement>('[data-credit-rest]')!,signal,people=>{
      fill(section.querySelector<HTMLElement>('[data-credit-rest]')!,people);
      if(section.matches('[data-credit-section=cast]'))paint(section,mode);
    });
    deferred.set(section,list);list.replace(JSON.parse(data?.textContent||'[]'));data?.remove();
    const toggle=section.querySelector('[data-credit-toggle]');
    toggle?.addEventListener('click',()=>{
      list.toggle(toggle.getAttribute('aria-expanded')!=='true');
    },{signal});
  });
  const update=(data:{people:DetailPerson[];organizations:DetailPerson[];character_status:DetailLoadState;credits_status?:string})=>{
    const groups=detailCredits({people:data.people,organizations:data.organizations,type:info.type} as DetailMedia);
    if(!groups.cast.length&&['failed','deferred'].includes(data.credits_status||''))return {state:'deferred',pending:true,retry_after:60,error:'unavailable'};
    const creatorRow=root.querySelector<HTMLElement>('[data-detail-creators]');
    const creators=detailCreators([...data.people,...data.organizations]);
    if(creatorRow&&creators.length){
      const nodes:Node[]=[];
      creators.forEach((person,index)=>{
        if(index)nodes.push(document.createTextNode(', '));
        if(person.url){const link=document.createElement('a');link.href=person.url;link.textContent=person.name;nodes.push(link);}
        else nodes.push(document.createTextNode(person.name));
      });
      creatorRow.replaceChildren(...nodes);creatorRow.hidden=false;
    }
    const preview=root.querySelector<HTMLElement>('[data-credit-preview]');if(preview)fill(preview,groups.preview);
    const rows={cast:groups.cast,crew:groups.principal,studios:groups.studios};
    root.querySelectorAll<HTMLElement>('[data-credit-section]').forEach(section=>{
      const people=rows[section.dataset.creditSection as keyof typeof rows]||[];
      const limit=Number(section.dataset.creditLimit||8);
      fill(section.querySelector<HTMLElement>('[data-credit-visible]')!,people.slice(0,limit));
      deferred.get(section)?.replace(people.slice(limit));
      section.hidden=!people.length;
      const toggle=section.querySelector<HTMLButtonElement>('[data-credit-toggle]')!;toggle.hidden=people.length<=limit;
    });
    metadataPending=data.character_status.pending===true||['partial','pending','deferred'].includes(data.character_status.state||'');
    containers.forEach(container=>paint(container,mode));
    status();return data.character_status;
  };
  const background=detailPeopleLoading(info.id,signal,update,(state,message)=>{
    error=state==='retry'?message||'People data is temporarily unavailable. Retrying automatically.':'';status();
  },{selected:()=>mode==='characters',needed:()=>metadataPending,
    primaryReady:()=>waitForDetailPortraits([...root.querySelectorAll<HTMLImageElement>('[data-credit-preview] img')],signal),warm});
  if(info.backgroundAvailable)background.start(!root.querySelector('[data-credit-preview] [data-person-id]'));
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-cast-mode]');
    if((event.target as Element).closest('[data-detail-people-retry]')){void background.retry();return;}
    if(!button||button.dataset.castMode===mode)return;
    mode=button.dataset.castMode==='characters'?'characters':'actors';
    if(mode==='characters'&&metadataPending&&info.backgroundAvailable)void background.characters();
    root.querySelectorAll<HTMLButtonElement>('[data-cast-mode]').forEach(control=>control.setAttribute('aria-pressed',String(control.dataset.castMode===mode)));
    const selected=mode;
    transitions.forEach((transition,container)=>void transition(()=>paint(container,selected)));
    status();
  },{signal});
}
