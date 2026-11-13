import {favoriteKey,favoriteOrderChanges,favoriteOrderSaved} from './favorite-order';
import {showActionFailure} from './action-failure';
import {editorStore,type EditorTitle} from './editor-store';
import {responsiveArtwork} from './responsive-artwork';
import {trackedWorkHref} from './tracker';
type Favorite = {id:number;type:string;title:string;poster:string|null};
export function initializeFavorites(root:HTMLElement, signal:AbortSignal) {
  const page = root.querySelector<HTMLElement>('.favorites-page');
  const grid = page?.querySelector<HTMLElement>('[data-favorites-grid]');
  if (!page || !grid) return;
  const cardKey=(card:HTMLElement)=>favoriteKey(card.dataset.favoriteType,card.dataset.mediaId);
  const reorder = page.querySelector<HTMLButtonElement>('[data-favorites-reorder]');
  const cancel = page.querySelector<HTMLButtonElement>('[data-favorites-cancel]');
  const actions = page.querySelector<HTMLElement>('.favorites-actions');
  const instructions = page.querySelector<HTMLElement>('[data-favorites-instructions]')!;
  const error = page.querySelector<HTMLElement>('[data-favorites-error]')!;
  const feedback = page.querySelector<HTMLElement>('[data-favorites-feedback]')!;
  let savedOrder = [...grid.querySelectorAll<HTMLElement>('[data-favorite-card]')].map(cardKey);
  let dragging: HTMLElement | null = null;
  const cards = () => [...grid.querySelectorAll<HTMLElement>('[data-favorite-card]')];
  const order = () => cards().map(cardKey);
  const groups = [...grid.querySelectorAll<HTMLElement>('[data-favorites-group]')];
  const previousCounts = JSON.parse(page.dataset.favoritesVisible || '{}') as Record<string,number>;
  const remaining = JSON.parse(page.querySelector('[data-favorites-remaining]')?.textContent || '{}') as Record<string, Favorite[]>;
  const cardTemplate = page.querySelector<HTMLTemplateElement>('[data-favorites-card-template]');
  function createCard(item: Favorite) {
    const card = cardTemplate!.content.firstElementChild!.cloneNode(true) as HTMLElement;
    card.dataset.mediaId = String(item.id);
    card.dataset.favoriteType = item.type;
    card.dataset.title = item.title;
    const link = card.querySelector<HTMLAnchorElement>('.favorite-poster')!;
    link.href = trackedWorkHref(item);
    link.title = item.title;
    link.setAttribute('aria-label', item.title);
    const image = responsiveArtwork(item.poster, {size:'w342',sizes:'(max-width:650px) 30vw, 150px',route:'direct'});
    if (image) {
      const poster = document.createElement('img');
      poster.alt = '';
      poster.loading = 'lazy';
      if (image.srcset) poster.srcset = image.srcset;
      if (image.sizes) poster.sizes = image.sizes;
      poster.src = image.src;
      link.replaceChildren(poster);
    }
    const remove = card.querySelector<HTMLButtonElement>('[data-favorite-remove]');
    if (remove) {
      remove.dataset.favoriteRemove = String(item.id);
      remove.title = `Remove ${item.title} from favorites`;
      remove.setAttribute('aria-label', remove.title);
    }
    return card;
  }
  function showMore(group: HTMLElement, count: number) {
    const queued = remaining[group.dataset.favoritesGroup!];
    if (!queued?.length || count <= 0) return;
    const added = queued.splice(0,count).map(createCard);
    group.querySelector('.favorites-grid')!.append(...added);
    group.querySelector<HTMLElement>('[data-favorites-more]')!.hidden = queued.length === 0;
    savedOrder = order();
    return added[0];
  }
  for (const group of groups) {
    const more = group.querySelector<HTMLAnchorElement>('[data-favorites-more]');
    if (!more) continue;
    const shown = group.querySelector('.favorites-grid')!.childElementCount;
    showMore(group, (previousCounts[group.dataset.favoritesGroup!] || 0) - shown);

    more.addEventListener('click', event => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const first = showMore(group, Number(more.dataset.favoritesMore));
      if (event.detail === 0) first?.querySelector<HTMLAnchorElement>('.favorite-poster')?.focus();
    }, {signal});
  }
  const report = (message: string) => { feedback.textContent = message; };
  const setReordering = (active: boolean) => {
    page.dataset.reordering = String(active);
    if (reorder) reorder.textContent = active ? 'Save Order' : 'Reorder';
    if (cancel) cancel.hidden = !active;
    instructions.hidden = !active;
    cards().forEach(card => { if (active) card.tabIndex = 0; else card.removeAttribute('tabindex'); });
    error.hidden = true;
    if (!active) root.dispatchEvent(new Event('anylist:profile-refresh', {bubbles:true}));
  };
  const updateEmpty = () => {
    const hasCards = cards().length > 0;
    grid.hidden = !hasCards;
    page.querySelector<HTMLElement>('[data-favorites-empty]')!.hidden = hasCards;
    const visibleGroups = [...page.querySelectorAll<HTMLElement>('[data-favorites-group]')].filter(group => {
      group.hidden = !group.querySelector('[data-favorite-card]:not([hidden])');
      return !group.hidden;
    });
    if (actions) {
      actions.hidden = !hasCards;
      if (hasCards) visibleGroups[0]?.querySelector('.favorites-group-heading')?.append(actions);
    }
  };

  reorder?.addEventListener('click', async () => {
    if (page.dataset.reordering !== 'true') {
      groups.forEach(group => showMore(group, Infinity));
      setReordering(true);
      return;
    }
    const current = order();
    if (current.join(',') === savedOrder.join(',')) { setReordering(false); return; }
    reorder.disabled = true;
    if (cancel) cancel.disabled = true;
    error.hidden = true;
    try {
      for(const change of favoriteOrderChanges(current,savedOrder)){
        const response=await fetch(change.path,{
          method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(change.body),
        });
        if(!response.ok){
          const details=await response.json().catch(()=>null);
          throw new Error(typeof details?.detail==='string'?details.detail:'Could not save favorite order.');
        }
        savedOrder=favoriteOrderSaved(savedOrder,current,change.path);
      }
      savedOrder = current;
      setReordering(false);
      report('Favorite order saved.');
    } catch (cause) {
      showActionFailure('favorite-order',null,cause);
    } finally {
      reorder.disabled = false;
      if (cancel) cancel.disabled = false;
    }
  }, {signal: signal});

  cancel?.addEventListener('click', () => {
    const byId = new Map(cards().map(card => [cardKey(card), card]));
    grid.querySelectorAll<HTMLElement>('.favorites-grid').forEach(groupGrid => {
      savedOrder.forEach(id => { const card = byId.get(id); if (card?.parentElement === groupGrid) groupGrid.append(card); });
    });
    setReordering(false);
    report('Favorite order unchanged.');
  }, {signal: signal});

  page.addEventListener('click', async event => {
    if (page.dataset.reordering === 'true' && (event.target as HTMLElement).closest('.favorite-card')) {
      event.preventDefault();
      return;
    }
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-favorite-remove]');
    if (!button || page.dataset.reordering === 'true') return;
    button.disabled = true;
    page.dataset.profileWriting='';
    error.hidden = true;
    try {
      if(button.closest<HTMLElement>('[data-favorite-card]')?.dataset.favoriteType==='game'){
        const title=await editorStore.loadSubject({kind:'game',catalogue_id:Number(button.dataset.favoriteRemove)});
        await editorStore.write(title,{favorite:false});
      }else{
      const response = await fetch(`/api/proxy/tracking/entry/${button.dataset.favoriteRemove}`, {
        method: 'PATCH', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({favorite: false}),
      });
      if (!response.ok) {
        const details = await response.json().catch(() => null);
        throw new Error(typeof details?.detail === 'string' ? details.detail : 'Could not remove this favorite.');
      }
      }
      const card = button.closest<HTMLElement>('[data-favorite-card]')!;
      const group = card.closest<HTMLElement>('[data-favorites-group]')!;
      const title = card.dataset.title || 'Title';
      card.remove();
      if (!group.querySelector('[data-favorite-card]')) {
        showMore(group, Number(group.querySelector<HTMLElement>('[data-favorites-more]')?.dataset.favoritesMore || 0));
      }
      savedOrder = order();
      updateEmpty();
      report(`${title} removed from favorites.`);
      document.dispatchEvent(new CustomEvent('anylist:data-changed'));

    } catch (cause) {
      showActionFailure('favorite',button.closest<HTMLElement>('[data-favorite-card]')?.dataset.title,cause);
      button.disabled = false;
    } finally {
      if(!page.querySelector('[data-favorite-remove]:disabled'))delete page.dataset.profileWriting;
      root.dispatchEvent(new Event('anylist:profile-refresh',{bubbles:true}));
    }
  }, {signal: signal});
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<EditorTitle>).detail;
    if(page.dataset.favoritesOwner!=='true'||title.type!=='game')return;
    const card=grid.querySelector<HTMLElement>(`[data-favorite-card][data-favorite-type="game"][data-media-id="${title.id}"]`);
    if(card){card.hidden=!title.favorite;updateEmpty();}
  },{signal:signal});

  page.addEventListener('pointerdown', event => {
    if (page.dataset.reordering !== 'true' || event.button !== 0) return;
    const card = (event.target as HTMLElement).closest<HTMLElement>('[data-favorite-card]');
    if (!card) return;
    event.preventDefault();
    dragging = card;
    card.classList.add('is-dragging');
    card.setPointerCapture(event.pointerId);
  }, {signal: signal});
  page.addEventListener('pointermove', event => {
    if (!dragging) return;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-favorite-card]');
    if (!target || target === dragging || target.parentElement !== dragging.parentElement) return;
    const current = [...dragging.parentElement!.querySelectorAll<HTMLElement>('[data-favorite-card]')];
    if (current.indexOf(dragging) < current.indexOf(target)) target.after(dragging);
    else target.before(dragging);
  }, {signal: signal});
  const stopDragging = () => { dragging?.classList.remove('is-dragging'); dragging = null; };
  root.addEventListener('anylist:panel-inactive', stopDragging, {signal});
  page.addEventListener('pointerup', stopDragging, {signal: signal});
  page.addEventListener('pointercancel', stopDragging, {signal: signal});
  page.addEventListener('keydown', event => {
    if (page.dataset.reordering !== 'true') return;
    if (event.key === 'Escape') { cancel?.click(); return; }
    if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
    const card = (event.target as HTMLElement).closest<HTMLElement>('[data-favorite-card]');
    if (!card) return;
    const current = [...card.parentElement!.querySelectorAll<HTMLElement>('[data-favorite-card]')];
    const index = current.indexOf(card);
    const direction = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
    const neighbor = current[index + direction];
    if (!neighbor) return;
    event.preventDefault();
    if (direction < 0) neighbor.before(card); else neighbor.after(card);
    card.focus();
    report(`${card.dataset.title} moved to position ${index + direction + 1}.`);
  }, {signal: signal});
}
